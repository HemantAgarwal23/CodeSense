from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.llm.langchain_client import LLMResponseParseError, generate_llm_review
from app.models.review import ReviewRequest
from app.utils.github import RepositoryProcessingError, process_github_repository
from app.utils.static_analysis import run_static_analysis

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB


class ReviewService:
    """End-to-end review orchestration service."""

    def __init__(self, max_concurrency: int = 4) -> None:
        self.max_concurrency = max(1, max_concurrency)

    async def run_review(self, req: ReviewRequest) -> dict[str, Any]:
        """Run review for either raw code input or a GitHub repository."""
        logger.info("Starting review pipeline. has_repo_url=%s", bool(req.repo_url))
        if req.repo_url:
            return await self._run_repo_review(req.repo_url, req.branch, req.max_files)
        return await self._run_raw_code_review(req.code_snippet or "")

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
                if max_files is not None:
                    file_paths = file_paths[:max_files]
                    logger.info("Applied max_files limit=%d. files_to_process=%d", max_files, len(file_paths))
                logger.info("Repository extracted files count=%d", len(file_paths))
                files = [
                    f for f in file_paths
                    if f.endswith((".py", ".js", ".ts", ".cpp"))
                    and "venv" not in f
                    and "__pycache__" not in f
                    and "node_modules" not in f
                ]
                file_results = await self._process_files(files)
                return self._build_response(file_results)
        except RepositoryProcessingError as exc:
            logger.warning("Repository processing failed: %s", exc)
            return self._empty_response()

    async def _run_raw_code_review(self, code: str) -> dict[str, Any]:
        """Process a raw code snippet as a synthetic single file."""
        with tempfile.TemporaryDirectory(prefix="review_raw_") as temp_dir:
            file_path = Path(temp_dir) / "inline_input.py"
            await asyncio.to_thread(file_path.write_text, code, "utf-8")
            logger.info("Processing raw code input via synthetic file: %s", file_path)
            file_results = await self._process_files([str(file_path.resolve())])
            return self._build_response(file_results)

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
            static_result = await asyncio.to_thread(run_static_analysis, str(path))

            base_result["_meta"]["errors_count"] = len(static_result.get("errors", []))
            base_result["_meta"]["warnings_count"] = len(static_result.get("warnings", []))

            logger.info(
                "Static analysis complete for %s. errors=%d warnings=%d",
                path,
                base_result["_meta"]["errors_count"],
                base_result["_meta"]["warnings_count"],
            )

            llm_result = await generate_llm_review(code, static_result)

            base_result["bugs"] = llm_result.get("bugs", [])
            base_result["suggestions"] = llm_result.get("suggestions", [])

            static_fixes = static_result.get("fixes", []) or []
            llm_fixes = llm_result.get("fixes", []) or []
            fixes = []
            fixes.extend(static_fixes if isinstance(static_fixes, list) else [static_fixes])
            fixes.extend(llm_fixes if isinstance(llm_fixes, list) else [llm_fixes])

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

        # Ensure final score cannot exceed rubric cap and remains consistent.
        final_score = min(final_score, 10, computed_final)

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
            },
            "final_score": 0.0,
        }

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
            },
            "final_score": final_score,
        }

        return payload
