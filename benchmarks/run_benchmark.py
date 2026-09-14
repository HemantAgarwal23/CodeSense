"""Measure how well CodeSense AI finds known bugs.

Runs every case in benchmarks/cases.py through up to three pipelines:
  static  AST static analysis only (offline, no API key)
  llm     LLM review only, without static-analysis context
  hybrid  the full pipeline the API serves: static + LLM + filtering

A finding is correct when it points at the labeled bug: its line is within one
line of the bug or, for findings without a line number, its text contains one of
the case's keywords. Every other finding counts against precision.

Usage:
    python -m benchmarks.run_benchmark
    python -m benchmarks.run_benchmark --modes static
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from app.core.config import get_settings
from app.llm.langchain_client import generate_llm_review
from app.models.review import ReviewRequest
from app.services.findings import entry_description, entry_line
from app.services.review_service import ReviewService
from app.utils.static_analysis import analyze_code
from benchmarks.cases import BUG_MARKER, CASES

MODES = ("static", "llm", "hybrid")
LLM_MODES = {"llm", "hybrid"}
LINE_TOLERANCE = 1
LLM_FAILURE_MARKERS = ("llm skipped", "llm failed")
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class Finding:
    description: str
    line: int | None


@dataclass
class CaseResult:
    case_id: str
    has_bug: bool
    findings: int = 0
    correct: int = 0
    error: str = ""

    @property
    def detected(self) -> bool:
        return self.correct > 0


def prepare_case(case: dict[str, Any]) -> tuple[str, int | None]:
    """Strip the bug marker from a case and return (code, 1-based bug line)."""
    lines: list[str] = []
    bug_line: int | None = None
    for number, line in enumerate(case["code"].strip("\n").splitlines(), start=1):
        stripped = line.rstrip()
        if stripped.endswith(BUG_MARKER):
            bug_line = number
            stripped = stripped[: -len(BUG_MARKER)].rstrip()
        lines.append(stripped)
    return "\n".join(lines) + "\n", bug_line


def _is_llm_failure(entry: Any) -> bool:
    return entry_description(entry).lower().startswith(LLM_FAILURE_MARKERS)


def _to_findings(entries: list[Any]) -> list[Finding]:
    findings: list[Finding] = []
    for entry in entries:
        if _is_llm_failure(entry):
            raise RuntimeError(entry_description(entry))
        description = entry_description(entry)
        if description:
            findings.append(Finding(description=description, line=entry_line(entry)))
    return findings


async def _static_findings(code: str) -> list[Finding]:
    result = analyze_code(code)
    return _to_findings([*result["bugs"], *result["runtime_risks"]])


async def _llm_findings(code: str) -> list[Finding]:
    result = await generate_llm_review(code, {"errors": [], "warnings": []})
    return _to_findings(result.get("bugs", []))


async def _hybrid_findings(code: str) -> list[Finding]:
    failures: list[str] = []

    async def tracked_review(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # The service filters LLM failure messages out, so record them here instead.
        result = await generate_llm_review(*args, **kwargs)
        failures.extend(entry_description(bug) for bug in result.get("bugs", []) if _is_llm_failure(bug))
        return result

    with patch("app.services.review_service.generate_llm_review", tracked_review):
        result = await ReviewService().run_review(ReviewRequest(code_snippet=code))
    if failures:
        raise RuntimeError(failures[0])

    findings = result.get("findings", {})
    return _to_findings([*findings.get("runtime_risks", []), *findings.get("code_issues", [])])


FINDERS = {"static": _static_findings, "llm": _llm_findings, "hybrid": _hybrid_findings}


def _matches(finding: Finding, bug_line: int, keywords: list[str]) -> bool:
    if finding.line is not None:
        return abs(finding.line - bug_line) <= LINE_TOLERANCE
    text = finding.description.lower()
    return any(keyword in text for keyword in keywords)


def score_case(case: dict[str, Any], findings: list[Finding], bug_line: int | None) -> CaseResult:
    keywords = [keyword.lower() for keyword in case["keywords"]]
    correct = 0
    if bug_line is not None:
        correct = sum(1 for finding in findings if _matches(finding, bug_line, keywords))
    return CaseResult(case_id=case["id"], has_bug=bug_line is not None, findings=len(findings), correct=correct)


async def evaluate_mode(mode: str, cases: list[dict[str, Any]], delay_seconds: float = 0.0) -> list[CaseResult]:
    """Run one pipeline over every case, recording failures instead of stopping."""
    finder = FINDERS[mode]
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        code, bug_line = prepare_case(case)
        try:
            results.append(score_case(case, await finder(code), bug_line))
        except Exception as exc:
            results.append(CaseResult(case_id=case["id"], has_bug=bug_line is not None, error=str(exc)[:200]))
        print(f"  [{mode}] {index}/{len(cases)} {case['id']}", flush=True)
        if mode in LLM_MODES and delay_seconds and index < len(cases):
            await asyncio.sleep(delay_seconds)
    return results


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    scored = [result for result in results if not result.error]
    buggy = [result for result in scored if result.has_bug]
    clean = [result for result in scored if not result.has_bug]
    detected = sum(1 for result in buggy if result.detected)
    flagged_clean = sum(1 for result in clean if result.findings)
    total_findings = sum(result.findings for result in scored)
    return {
        "cases_scored": len(scored),
        "cases_failed": len(results) - len(scored),
        "buggy_cases": len(buggy),
        "clean_cases": len(clean),
        "bugs_detected": detected,
        "recall": _ratio(detected, len(buggy)),
        "findings": total_findings,
        "correct_findings": sum(result.correct for result in scored),
        "precision": _ratio(sum(result.correct for result in scored), total_findings),
        "clean_cases_flagged": flagged_clean,
        "false_alarm_rate": _ratio(flagged_clean, len(clean)),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _case_cell(result: CaseResult) -> str:
    if result.error:
        return "error"
    if result.has_bug:
        return "found" if result.detected else "missed"
    return "clean" if not result.findings else f"{result.findings} false alarm(s)"


def render_markdown(report: dict[str, Any]) -> str:
    modes = list(report["summaries"])
    lines = [
        "# CodeSense AI benchmark",
        "",
        f"- Run: {report['generated_at']}",
        f"- Cases: {report['case_count']} ({report['buggy_case_count']} with one known bug, "
        f"{report['case_count'] - report['buggy_case_count']} clean)",
        f"- LLM: {report['llm_model']} (falls back to {report['llm_fallback_model']} on errors or rate limits)",
        "",
        "| Mode | Recall (bugs found) | Precision (findings that were correct) | Clean snippets flagged | Failed cases |",
        "|---|---|---|---|---|",
    ]
    for mode, summary in report["summaries"].items():
        lines.append(
            f"| {mode} | {_percent(summary['recall'])} ({summary['bugs_detected']}/{summary['buggy_cases']}) "
            f"| {_percent(summary['precision'])} ({summary['correct_findings']}/{summary['findings']}) "
            f"| {summary['clean_cases_flagged']}/{summary['clean_cases']} | {summary['cases_failed']} |"
        )

    lines += ["", "## Per case", "", "| Case | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    by_mode = {mode: {row["case_id"]: CaseResult(**row) for row in report["results"][mode]} for mode in modes}
    for case_id in report["case_ids"]:
        cells = [_case_cell(by_mode[mode][case_id]) for mode in modes]
        lines.append(f"| {case_id} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def groq_setup_problem(api_key: str, models: list[str]) -> str | None:
    """Return why LLM modes can't run (bad key or unknown model), or None when Groq is usable."""
    if not api_key:
        return "GROQ_API_KEY is not set"
    try:
        response = httpx.get(GROQ_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    except httpx.HTTPError as exc:
        return f"Could not reach Groq ({exc.__class__.__name__})"
    if response.status_code in {401, 403}:
        return f"Groq rejected GROQ_API_KEY (HTTP {response.status_code}); it may be expired"
    if not response.is_success:
        return f"Groq model list request failed (HTTP {response.status_code})"

    available = {model["id"] for model in response.json().get("data", [])}
    missing = [model for model in models if model and model not in available]
    if missing:
        return (
            f"Groq has no model named {', '.join(missing)}. "
            f"Set LLM_MODEL / LLM_FALLBACK_MODEL to one of: {', '.join(sorted(available))}"
        )
    return None


async def run(modes: list[str], cases: list[dict[str, Any]], delay_seconds: float) -> dict[str, Any]:
    settings = get_settings()
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "llm_model": settings.llm_model,
        "llm_fallback_model": settings.llm_fallback_model,
        "case_count": len(cases),
        "buggy_case_count": sum(1 for case in cases if case["keywords"]),
        "case_ids": [case["id"] for case in cases],
        "summaries": {},
        "results": {},
    }
    for mode in modes:
        results = await evaluate_mode(mode, cases, delay_seconds)
        report["summaries"][mode] = summarize(results)
        report["results"][mode] = [asdict(result) for result in results]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark CodeSense AI bug detection.")
    parser.add_argument("--modes", default=",".join(MODES), help="Comma-separated: static, llm, hybrid.")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between LLM cases, for Groq rate limits.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases.")
    args = parser.parse_args()

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    unknown = sorted(set(modes) - set(MODES))
    if unknown:
        parser.error(f"Unknown mode(s): {', '.join(unknown)}")
    if LLM_MODES & set(modes):
        # A bad key or retired model makes every case retry and fail, which takes a long time.
        settings = get_settings()
        problem = groq_setup_problem(settings.groq_api_key, [settings.llm_model, settings.llm_fallback_model])
        if problem:
            parser.error(f"{problem}. Fix .env or run with --modes static.")

    logging.basicConfig(level=logging.ERROR)
    # Failed cases are recorded in the report; the client's per-attempt tracebacks only add noise.
    logging.getLogger("app.llm.langchain_client").setLevel(logging.CRITICAL)
    cases = CASES[: args.limit] if args.limit else CASES
    report = asyncio.run(run(modes, cases, args.delay))

    RESULTS_DIR.mkdir(exist_ok=True)
    markdown = render_markdown(report)
    (RESULTS_DIR / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (RESULTS_DIR / "latest.md").write_text(markdown, encoding="utf-8")
    print()
    print(markdown)


if __name__ == "__main__":
    main()
