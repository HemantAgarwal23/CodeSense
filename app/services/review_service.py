from __future__ import annotations

import ast
import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.llm.langchain_client import LLMResponseParseError, generate_llm_review
from app.models.review import ReviewRequest
from app.services.findings import build_findings, entry_description, entry_line
from app.utils.github import RepositoryProcessingError, process_github_repository
from app.utils.github_pr import fetch_pull_request, post_review_comments
from app.utils.static_analysis import run_static_analysis

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB
# Drops only findings the reviewer itself marked low confidence (low=0.5, medium=0.7, high=0.9).
MIN_CONFIDENCE_SCORE = 0.6
SUPPORTED_EXTENSIONS = (".py", ".js", ".ts", ".tsx", ".cpp", ".java")
MAX_INLINE_COMMENTS = 30
# Static "warnings" that describe analyzer limits rather than problems in the code.
STATIC_NOTICE_SYMBOLS = {"language-limited-static-analysis", "file-too-large", "invalid-path", "read-failure"}
GENERIC_ADVICE_MARKERS = (
    "add type hints",
    "add docstring",
    "add docstrings",
    "add error handling",
    "improve readability",
    "use logging",
)
HIGH_SIGNAL_SYMBOLS = {
    "sql-injection-risk",
    "unsafe-dynamic-exec",
    "unsafe-subprocess-shell-true",
    "hardcoded-secret",
    "division-by-zero",
    "missing-return",
    "inconsistent-return-path",
    "resource-leak-risk",
    "infinite-recursion-risk",
    "late-binding-lambda",
    "broad-except",
    "exception-pass-swallow",
}
LOW_PROD_IMPACT_NOTE = "Context: test/example file (lower production impact)"
TEST_SUPPRESS_LOGIC_SYMBOLS = {
    "missing-return",
    "inconsistent-return",
    "inconsistent-return-path",
    "unreachable-code",
}
PRESERVE_EVERYWHERE_SYMBOLS = {
    "unsafe-subprocess-shell-true",
    "subprocess-risk",
    "hardcoded-secret",
    "sql-injection-risk",
    "division-by-zero",
    "infinite-recursion-risk",
    "unsafe-dynamic-exec",
    "unsafe-pickle",
    "resource-leak-risk",
    "broad-except",
    "exception-pass-swallow",
}
LOGIC_NOISE_SYMBOLS = {
    "missing-return",
    "inconsistent-return",
    "inconsistent-return-path",
    "unreachable-code",
}
DOWNGRADE_RUNTIME_RISK_SYMBOLS = {
    "possible-keyerror",
    "possible-attributeerror",
    "possible-valueerror",
}


class ReviewService:
    """End-to-end review orchestration service."""

    def __init__(self, max_concurrency: int = 4) -> None:
        self.max_concurrency = max(1, max_concurrency)

    async def run_review(self, req: ReviewRequest) -> dict[str, Any]:
        """Run review for either raw code input or a GitHub repository."""
        logger.info("Starting review pipeline. has_repo_url=%s", bool(req.repo_url))
        if req.repo_url:
            result = await self._run_repo_review(req.repo_url, req.branch, req.max_files)
        else:
            result = await self._run_raw_code_review(req.code_snippet or "", req.filename)
        result["findings"] = build_findings(result.get("files", []), req.code_snippet or "")
        return result

    async def _run_repo_review(
        self,
        repo_url: str,
        branch: str,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        """Process all relevant source files from a repository."""
        try:
            with tempfile.TemporaryDirectory(prefix="review_repo_") as output_dir:
                logger.info("Processing repository input. branch=%s", branch)
                file_paths = await asyncio.to_thread(
                    process_github_repository,
                    repo_url,
                    branch,
                    output_dir,
                )
                logger.info("Repository extracted files count=%d", len(file_paths))
                files = [
                    f for f in file_paths
                    if f.endswith(SUPPORTED_EXTENSIONS)
                    and "venv" not in f
                    and "__pycache__" not in f
                    and "node_modules" not in f
                ]
                file_limit = self._file_limit(max_files)
                if len(files) > file_limit:
                    logger.info("Applied file limit=%d. supported_files=%d", file_limit, len(files))
                    files = files[:file_limit]
                if not files:
                    logger.warning("Repository produced zero supported files after filtering.")
                    return {
                        "files": [
                            {
                                "file": "__repository__",
                                "bugs": [],
                                "fixes": [],
                                "suggestions": [
                                    "No supported source files found (.py, .js, .ts, .tsx, .cpp, .java)."
                                ],
                                "score": self._base_score(),
                            }
                        ],
                        "summary": {
                            "total_errors": 0,
                            "total_warnings": 0,
                            "final_score": 0.0,
                            "status": "failed",
                        },
                        "final_score": 0.0,
                    }
                file_results = await self._process_files(files)
                self._relativize_paths(file_results, Path(output_dir).resolve())
                return self._build_response(file_results)
        except RepositoryProcessingError as exc:
            logger.warning("Repository processing failed: %s", exc)
            return {
                "files": [
                    {
                        "file": "__repository__",
                        "bugs": [],
                        "fixes": [],
                        "suggestions": [f"Repository processing failed: {exc}"],
                        "score": self._base_score(),
                    }
                ],
                "summary": {
                    "total_errors": 0,
                    "total_warnings": 0,
                    "final_score": 0.0,
                    "status": "failed",
                },
                "final_score": 0.0,
            }

    async def _run_raw_code_review(self, code: str, filename: str | None = None) -> dict[str, Any]:
        """Process a raw code snippet as a synthetic single file named after its language."""
        with tempfile.TemporaryDirectory(prefix="review_raw_") as temp_dir:
            file_path = Path(temp_dir) / self._snippet_filename(code, filename)
            await asyncio.to_thread(file_path.write_text, code, "utf-8")
            logger.info("Processing raw code input via synthetic file: %s", file_path)
            file_results = await self._process_files([str(file_path.resolve())])
            self._relativize_paths(file_results, Path(temp_dir).resolve())
            return self._build_response(file_results)

    async def run_pr_review(
        self,
        pr_url: str,
        max_files: int | None = None,
        post_comments: bool = False,
    ) -> dict[str, Any]:
        """Review the supported files a GitHub pull request changes."""
        token = get_settings().github_token
        pull_request = await fetch_pull_request(
            pr_url,
            token=token,
            supported_extensions=SUPPORTED_EXTENSIONS,
            max_files=self._file_limit(max_files),
        )
        logger.info("Reviewing pull request %s. files=%d", pull_request.html_url, len(pull_request.files))

        with tempfile.TemporaryDirectory(prefix="review_pr_") as temp_dir:
            root = Path(temp_dir).resolve()
            file_paths: list[str] = []
            for changed_file in pull_request.files:
                target = (root / changed_file.path).resolve()
                if not target.is_relative_to(root):
                    logger.warning("Skipping pull request file outside review root: %s", changed_file.path)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(target.write_text, changed_file.content, "utf-8")
                file_paths.append(str(target))
            file_results = await self._process_files(file_paths)
            self._relativize_paths(file_results, root)

        result = self._build_response(file_results)
        changed_lines = {changed_file.path: changed_file.changed_lines for changed_file in pull_request.files}
        comments = self._build_inline_comments(result["files"], changed_lines)
        comments_posted = await post_review_comments(pull_request, comments, token) if post_comments else 0

        result["pull_request"] = {
            "title": pull_request.title,
            "url": pull_request.html_url,
            "head_sha": pull_request.head_sha,
            "files_changed": pull_request.supported_files_changed,
            "files_reviewed": len(file_paths),
            "comments_posted": comments_posted,
        }
        result["review_comments"] = comments
        result["findings"] = build_findings(result["files"])
        return result

    @staticmethod
    def _snippet_filename(code: str, filename: str | None) -> str:
        """Pick a file name whose extension matches the snippet's language.

        The Python AST analyzer reports bogus syntax errors on other languages. An uploaded
        file's extension wins; otherwise code that parses as Python is Python, and anything
        else is classified by obvious language markers (falling back to Python, so real
        Python syntax errors are still reported).
        """
        name = Path(filename or "").name
        if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
            return name
        try:
            ast.parse(code)
            return "snippet.py"
        except (SyntaxError, ValueError):
            pass
        language_markers = (
            (".java", ("public class ", "System.out.println", "public static void main")),
            (".cpp", ("#include", "std::", "int main(")),
            (".ts", ("interface ", ": string", ": number")),
            (".js", ("function ", "const ", "let ", "=>", "console.log", "require(")),
        )
        for suffix, markers in language_markers:
            if any(marker in code for marker in markers):
                return f"snippet{suffix}"
        return "snippet.py"

    @staticmethod
    def _file_limit(max_files: int | None) -> int:
        """Cap the requested file count at MAX_REPO_FILES, since each file costs one LLM call."""
        limit = get_settings().max_repo_files
        return min(max_files, limit) if max_files is not None else limit

    @staticmethod
    def _relativize_paths(file_results: list[dict[str, Any]], root: Path) -> None:
        """Replace temporary absolute paths with paths relative to the review root."""

        def relative(value: Any) -> Any:
            if not isinstance(value, str) or not value:
                return value
            try:
                return Path(value).resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                return value

        for file_result in file_results:
            file_result["file"] = relative(file_result.get("file"))
            for field in ("bugs", "fixes", "suggestions"):
                for entry in file_result.get(field) or []:
                    if isinstance(entry, dict):
                        for key in ("file", "path"):
                            if key in entry:
                                entry[key] = relative(entry[key])

    @staticmethod
    def _build_inline_comments(
        files: list[dict[str, Any]],
        changed_lines: dict[str, set[int]],
    ) -> list[dict[str, Any]]:
        """Turn findings that sit on lines the pull request changed into review comments."""
        comments: list[dict[str, Any]] = []
        seen: set[tuple[str, int, str]] = set()
        for file_result in files:
            path = str(file_result.get("file") or "")
            lines = changed_lines.get(path, set())
            for entry in [*(file_result.get("bugs") or []), *(file_result.get("suggestions") or [])]:
                line = entry_line(entry)
                description = entry_description(entry)
                if line is None or line not in lines or not description:
                    continue
                key = (path, line, description.lower())
                if key in seen:
                    continue
                seen.add(key)
                severity = str(entry.get("severity") or "low").lower()
                comments.append({"path": path, "line": line, "body": f"**CodeSense AI** ({severity}): {description}"})
                if len(comments) >= MAX_INLINE_COMMENTS:
                    return comments
        return comments

    async def _process_files(self, file_paths: list[str]) -> list[dict[str, Any]]:
        """Process files concurrently with bounded parallelism."""
        if not file_paths:
            return []
        semaphore = asyncio.Semaphore(self.max_concurrency)
        tasks = [self._process_file_with_semaphore(path, semaphore) for path in file_paths]
        return await asyncio.gather(*tasks)

    async def _process_file_with_semaphore(
        self,
        file_path: str,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        async with semaphore:
            return await self._process_single_file(file_path)

    async def _process_single_file(self, file_path: str) -> dict[str, Any]:
        """Read, analyze, and review one file with graceful failure handling."""
        
        path = Path(file_path).resolve()
        logger.info("Reviewing file: %s", path)
        base_result = self._base_file_result(path)

        try:
            if not path.exists() or not path.is_file():
                base_result["suggestions"].append("File path is invalid or unreadable.")
                logger.warning("Skipping invalid file path: %s", path)
                return base_result

            if path.stat().st_size > MAX_FILE_SIZE_BYTES:
                base_result["suggestions"].append("Skipped file larger than 1 MB.")
                logger.info("Skipping large file (>1MB): %s", path)
                return base_result

            code = await asyncio.to_thread(path.read_text, "utf-8", "ignore")
            language = self._detect_language_from_path(path)
            if language == "python":
                static_result = await asyncio.to_thread(run_static_analysis, str(path))
            else:
                # The AST analyzer only parses Python; on other languages it reports bogus syntax errors.
                static_result = {"errors": [], "warnings": [], "fixes": [], "suggestions": [], "score": 0.0}
                static_result["warnings"] = static_result.get("warnings", []) + [{
                    "type": "warning",
                    "line": 0,
                    "symbol": "language-limited-static-analysis",
                    "message": f"Static analysis depth is limited for {language}; LLM review prioritized.",
                    "path": str(path),
                }]

            base_result["_meta"]["errors_count"] = len(static_result.get("errors", []))
            base_result["_meta"]["warnings_count"] = len(static_result.get("warnings", []))

            logger.info(
                "Static analysis complete for %s. errors=%d warnings=%d",
                path,
                base_result["_meta"]["errors_count"],
                base_result["_meta"]["warnings_count"],
            )

            llm_result = await generate_llm_review(code, static_result)

            # Failure and skip placeholders ("LLM failed ...") are not bugs in the user's code.
            llm_unavailable = llm_result.get("status") in {"failed", "skipped"}
            llm_bugs = [] if llm_unavailable else (llm_result.get("bugs", []) or [])
            llm_suggestions = llm_result.get("suggestions", []) or []
            static_errors = static_result.get("errors", []) or []
            static_warnings = static_result.get("warnings", []) or []

            # Static runtime risks (division by zero, leaks, ...) are bugs, not suggestions.
            static_runtime_risks = [
                warning for warning in static_warnings
                if not isinstance(warning, dict) or warning.get("symbol") not in STATIC_NOTICE_SYMBOLS
            ]
            static_notices = [
                warning for warning in static_warnings
                if isinstance(warning, dict) and warning.get("symbol") in STATIC_NOTICE_SYMBOLS
            ]

            base_result["bugs"] = self._annotate_entries(llm_bugs, source="llm", kind="bug", file_path=str(path))
            base_result["bugs"].extend(
                self._annotate_entries(
                    [*static_errors, *static_runtime_risks],
                    source="static",
                    kind="bug",
                    file_path=str(path),
                )
            )
            base_result["suggestions"] = self._annotate_entries(
                llm_suggestions,
                source="llm",
                kind="suggestion",
                file_path=str(path),
            )
            base_result["suggestions"].extend(
                self._annotate_entries(static_notices, source="static", kind="suggestion", file_path=str(path))
            )

            static_fixes = static_result.get("fixes", []) or []
            llm_fixes = llm_result.get("fixes", []) or []
            fixes = []
            static_fixes_list = static_fixes if isinstance(static_fixes, list) else [static_fixes]
            llm_fixes_list = llm_fixes if isinstance(llm_fixes, list) else [llm_fixes]
            fixes.extend(self._annotate_entries(static_fixes_list, source="static", kind="fix", file_path=str(path)))
            fixes.extend(self._annotate_entries(llm_fixes_list, source="llm", kind="fix", file_path=str(path)))

            issues_exist = (
                base_result["bugs"]
                or static_result.get("errors")
                or static_result.get("warnings")
            )

            if issues_exist and not fixes:
                fixes = [{
                    "title": "Manual Fix Required",
                    "description": "Fix could not be auto-generated. Please review the issue manually."
                }]

            base_result["fixes"] = fixes

            base_result["score"] = self._normalize_score(
                llm_result.get("score", base_result["score"])
            )
            rubric_empty = not any(value for key, value in base_result["score"].items() if key != "final_score")
            if llm_unavailable or rubric_empty:
                # No usable LLM rubric (a failure, or placeholder zeros echoed back): score from static analysis.
                fallback_score = static_result.get("score") if language == "python" else 10.0
                base_result["score"]["final_score"] = int(round(float(fallback_score or 0)))
            base_result["score"]["final_score"] = int(
                min(
                    base_result["score"]["final_score"],
                    self._heuristic_score_from_findings(base_result),
                )
            )

            logger.info(
                "LLM review complete for %s. final_score=%s",
                path,
                base_result["score"]["final_score"],
            )

            return base_result

        except LLMResponseParseError:
            logger.warning("LLM response parsing failed for file: %s", path)
            base_result["suggestions"].append("LLM parsing failed for this file.")
            return base_result

        except Exception:
            logger.exception("Failed to process file: %s", path)
            base_result["suggestions"].append("File processing failed unexpectedly.")
            return base_result
    @staticmethod
    def _base_score() -> dict[str, int]:
        return {
            "readability": 0,
            "correctness": 0,
            "efficiency": 0,
            "best_practices": 0,
            "maintainability": 0,
            "final_score": 0,
        }

    def _base_file_result(self, path: Path) -> dict[str, Any]:
        return {
            "file": str(path),
            "bugs": [],
            "fixes": [],
            "suggestions": [],
            "score": self._base_score(),
            "_meta": {"errors_count": 0, "warnings_count": 0},
        }

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.strip().lower().split())

    def _entry_dedupe_key(self, entry: Any) -> str:
        """Build a stable deduplication key, preferring human-readable description."""
        if isinstance(entry, str):
            return self._normalize_text(entry)

        if isinstance(entry, dict):
            for field in ("message", "description", "title", "reason", "text"):
                field_value = entry.get(field)
                if isinstance(field_value, str) and field_value.strip():
                    return self._normalize_text(field_value)
            try:
                return self._normalize_text(json.dumps(entry, sort_keys=True, ensure_ascii=False))
            except (TypeError, ValueError):
                return self._normalize_text(str(entry))

        return self._normalize_text(str(entry))

    def _deduplicate_entries(
        self,
        entries: list[Any],
        seen: set[str],
    ) -> list[Any]:
        """Remove duplicates while preserving order."""
        unique: list[Any] = []
        for entry in entries:
            key = self._entry_dedupe_key(entry)
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            unique.append(entry)
        return unique

    @staticmethod
    def _to_bounded_number(value: Any, low: float, high: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return low
        return min(max(number, low), high)

    def _normalize_score(self, score: Any) -> dict[str, int]:
        if not isinstance(score, dict):
            return self._base_score()

        readability = int(self._to_bounded_number(score.get("readability", 0), 0, 2))
        correctness = int(self._to_bounded_number(score.get("correctness", 0), 0, 3))
        efficiency = int(self._to_bounded_number(score.get("efficiency", 0), 0, 2))
        best_practices = int(self._to_bounded_number(score.get("best_practices", 0), 0, 2))
        maintainability = int(self._to_bounded_number(score.get("maintainability", 0), 0, 1))
        computed_final = readability + correctness + efficiency + best_practices + maintainability
        final_score = int(self._to_bounded_number(score.get("final_score", computed_final), 0, 10))

        # Keep the final score consistent with the rubric, unless the rubric is empty
        # (the score then came from static analysis instead of the LLM).
        final_score = min(final_score, 10, computed_final) if computed_final else min(final_score, 10)

        return {
            "readability": readability,
            "correctness": correctness,
            "efficiency": efficiency,
            "best_practices": best_practices,
            "maintainability": maintainability,
            "final_score": final_score,
        }

    @staticmethod
    def _adjust_score_from_findings(item: dict) -> float:
        bugs = len(item.get("bugs", []) or [])
        suggestions = len(item.get("suggestions", []) or [])
        errors = item.get("_meta", {}).get("errors_count", 0)

        score = 10
        score -= bugs * 2
        score -= errors * 1
        score -= suggestions * 0.3  # small penalty

        return max(0, score)

    @staticmethod
    def _empty_response() -> dict[str, Any]:
        return {
            "files": [],
            "summary": {
                "total_errors": 0,
                "total_warnings": 0,
                "final_score": 0.0,
                "status": "ok",
            },
            "final_score": 0.0,
        }

    @staticmethod
    def _detect_language_from_path(path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".cpp": "cpp",
            ".java": "java",
        }.get(suffix, "unknown")

    def _severity_from_text(self, text: str) -> str:
        lowered = (text or "").lower()
        if any(token in lowered for token in ("critical", "rce", "sql injection", "auth bypass")):
            return "critical"
        if any(token in lowered for token in ("error", "exception", "crash", "division by zero", "unsafe")):
            return "high"
        if any(token in lowered for token in ("warning", "risk", "may", "potential")):
            return "medium"
        return "low"

    @staticmethod
    def _confidence_for_source(source: str) -> str:
        return "high" if source == "static" else "medium"

    @staticmethod
    def _confidence_score(label: str) -> float:
        mapping = {"high": 0.9, "medium": 0.7, "low": 0.5}
        return mapping.get(str(label or "").lower(), 0.5)

    @staticmethod
    def _has_concrete_evidence(entry: dict[str, Any]) -> bool:
        line = entry.get("line")
        symbol = str(entry.get("symbol") or "").strip()
        file_ref = str(entry.get("file") or entry.get("path") or "").strip()
        description = str(
            entry.get("description")
            or entry.get("message")
            or entry.get("title")
            or entry.get("reason")
            or ""
        ).lower()
        has_line = isinstance(line, int) and line > 0
        has_symbol = bool(symbol)
        has_file = bool(file_ref)
        mentions_runtime = any(
            token in description for token in ("exception", "runtime", "division by zero", "null", "none", "indexerror", "keyerror")
        )
        return has_line or has_symbol or (has_file and mentions_runtime)

    def _is_generic_advice(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(marker in lowered for marker in GENERIC_ADVICE_MARKERS)

    def _filter_actionable_entries(self, entries: list[Any], kind: str) -> list[Any]:
        """Drop findings the reviewer marked low confidence, plus generic advice without evidence.

        The gate deliberately ignores how severe a finding sounds: keying on words like
        "error" dropped correct LLM bugs (path traversal, shared class state) in the benchmark.
        """
        filtered: list[Any] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            symbol = str(entry.get("symbol") or "").strip().lower()
            source = str(entry.get("source") or "").strip().lower()
            trusted = source == "static" or symbol in HIGH_SIGNAL_SYMBOLS
            if not trusted and self._confidence_score(entry.get("confidence")) < MIN_CONFIDENCE_SCORE:
                continue

            description = str(
                entry.get("description")
                or entry.get("message")
                or entry.get("title")
                or entry.get("reason")
                or ""
            )

            # Enforce "no generic coding advice" unless backed by concrete evidence.
            if kind == "suggestion" and self._is_generic_advice(description):
                if not self._has_concrete_evidence(entry):
                    continue
            filtered.append(entry)
        return filtered

    @staticmethod
    def is_test_or_example_file(file_path: str) -> bool:
        lowered = str(file_path or "").replace("\\", "/").lower()
        name = Path(file_path).name.lower() if file_path else ""
        return (
            "/tests/" in lowered
            or "/examples/" in lowered
            or "/demo/" in lowered
            or "/samples/" in lowered
            or name.startswith("test_")
            or name.endswith("_test.py")
        )

    @classmethod
    def _file_context(cls, file_path: str) -> str:
        lowered = str(file_path or "").replace("\\", "/").lower()
        name = Path(file_path).name.lower() if file_path else ""
        if cls.is_test_or_example_file(file_path) or name == "conftest.py":
            if "/examples/" in lowered or "/example/" in lowered or "/demo/" in lowered or "/samples/" in lowered:
                return "example"
            return "test"
        return "production"

    def _apply_context_rules(self, entries: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for entry in entries:
            symbol = str(entry.get("symbol") or "").lower()
            file_path = str(entry.get("file") or entry.get("path") or "")
            context = self._file_context(file_path)
            is_preserved = symbol in PRESERVE_EVERYWHERE_SYMBOLS

            if context == "test" and not is_preserved:
                if symbol in TEST_SUPPRESS_LOGIC_SYMBOLS or symbol in LOGIC_NOISE_SYMBOLS:
                    continue

            if context in {"test", "example"} and not is_preserved:
                # Suppress logical-flow noise for non-production contexts.
                if symbol in LOGIC_NOISE_SYMBOLS or (
                    kind == "bug" and str(entry.get("category") or "").lower() == "logic"
                ):
                    continue

                # Lower production impact for example/test-only findings.
                severity = str(entry.get("severity") or "low").lower()
                if severity in {"critical", "high"}:
                    entry["severity"] = "medium"
                elif severity == "medium":
                    entry["severity"] = "low"
                if float(entry.get("confidence_score") or 0.0) >= 0.9:
                    entry["confidence"] = "medium"
                    entry["confidence_score"] = self._confidence_score("medium")

                # Explicit downgrade for common runtime risk noise in tests/examples.
                description_lower = str(
                    entry.get("description")
                    or entry.get("message")
                    or entry.get("title")
                    or entry.get("reason")
                    or ""
                ).lower()
                if (
                    symbol in DOWNGRADE_RUNTIME_RISK_SYMBOLS
                    or "keyerror" in description_lower
                    or "attributeerror" in description_lower
                    or "valueerror" in description_lower
                ):
                    entry["severity"] = "low"
                    entry["confidence"] = "low"
                    entry["confidence_score"] = self._confidence_score("low")

                description = str(
                    entry.get("description")
                    or entry.get("message")
                    or entry.get("title")
                    or entry.get("reason")
                    or ""
                )
                if LOW_PROD_IMPACT_NOTE.lower() not in description.lower():
                    entry["description"] = f"{description} ({LOW_PROD_IMPACT_NOTE})".strip()

            adjusted.append(entry)
        return adjusted

    def _annotate_entries(self, entries: list[Any], source: str, kind: str, file_path: str) -> list[Any]:
        annotated: list[Any] = []
        for entry in entries:
            if isinstance(entry, dict):
                enriched = dict(entry)
                line = entry_line(enriched)
                if line is not None:
                    enriched["line"] = line
                message = str(
                    enriched.get("description")
                    or enriched.get("message")
                    or enriched.get("title")
                    or enriched.get("reason")
                    or enriched.get("issue")
                    or enriched.get("fix")
                    or ""
                )
                default_confidence = self._confidence_for_source(source)
                if source == "llm":
                    provisional = dict(enriched)
                    provisional["description"] = message
                    provisional["file"] = str(enriched.get("file") or enriched.get("path") or file_path)
                    default_confidence = "high" if self._has_concrete_evidence(provisional) else "medium"
                enriched["severity"] = str(enriched.get("severity") or self._severity_from_text(message)).lower()
                confidence = str(enriched.get("confidence") or "").strip().lower()
                enriched["confidence"] = confidence if confidence in {"high", "medium", "low"} else default_confidence
                enriched["confidence_score"] = self._confidence_score(enriched["confidence"])
                enriched["source"] = enriched.get("source") or source
                enriched["kind"] = kind
                enriched["file"] = str(enriched.get("file") or enriched.get("path") or file_path)
                annotated.append(enriched)
                continue
            text = str(entry)
            confidence = self._confidence_for_source(source)
            if source == "llm":
                provisional = {"description": text, "file": file_path}
                confidence = "high" if self._has_concrete_evidence(provisional) else "medium"
            annotated_entry = {
                "description": text,
                "severity": self._severity_from_text(text),
                "confidence": confidence,
                "confidence_score": self._confidence_score(confidence),
                "source": source,
                "kind": kind,
                "file": file_path,
            }
            if kind == "fix" and text.strip():
                # Models sometimes return a fix as bare corrected code rather than an object.
                first_line = text.strip().splitlines()[0][:80]
                annotated_entry.update({"description": f"Corrected code: {first_line}", "code": text})
            annotated.append(annotated_entry)
        filtered = self._filter_actionable_entries(annotated, kind=kind)
        return self._apply_context_rules(filtered, kind=kind)

    def _heuristic_score_from_findings(self, item: dict[str, Any]) -> float:
        bugs = len(item.get("bugs", []) or [])
        suggestions = len(item.get("suggestions", []) or [])
        errors = int(item.get("_meta", {}).get("errors_count", 0) or 0)
        warnings = int(item.get("_meta", {}).get("warnings_count", 0) or 0)
        score = 10 - (bugs * 1.6) - (errors * 1.2) - (warnings * 0.6) - (suggestions * 0.2)
        return max(0.0, min(10.0, score))

    def _build_response(self, file_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Build final response payload and aggregate summary metrics."""

        if not file_results:
            return self._empty_response()

        total_errors = sum(item.get("_meta", {}).get("errors_count", 0) for item in file_results)
        total_warnings = sum(item.get("_meta", {}).get("warnings_count", 0) for item in file_results)

        seen_bugs: set[str] = set()
        seen_fixes: set[str] = set()
        seen_suggestions: set[str] = set()

        scored_files = []

        for item in file_results:
            item["bugs"] = self._deduplicate_entries(item.get("bugs", []), seen_bugs)
            item["fixes"] = self._deduplicate_entries(item.get("fixes", []), seen_fixes)
            item["suggestions"] = self._deduplicate_entries(item.get("suggestions", []), seen_suggestions)

            normalized = self._normalize_score(item.get("score"))

            # fallback if score missing
            if normalized.get("final_score") is None:
                normalized["final_score"] = self._adjust_score_from_findings(item)

            item["score"] = normalized
            scored_files.append(normalized)

        if scored_files:
            final_score = round(
                sum(float(score["final_score"]) for score in scored_files) / len(scored_files),
                2,
            )
        else:
            final_score = 0.0

        final_score = self._to_bounded_number(final_score, 0, 10)

        cleaned_files = []
        for item in file_results:
            cleaned = dict(item)
            cleaned.pop("_meta", None)
            cleaned_files.append(cleaned)

        payload = {
            "files": cleaned_files,
            "summary": {
                "total_errors": total_errors,
                "total_warnings": total_warnings,
                "final_score": final_score,
                "status": "ok",
            },
            "final_score": final_score,
        }

        return payload
