"""Turn raw per-file review output into display-ready findings.

Merging, filtering, and deduplicating used to happen in the React UI. Doing it
here gives the API, the benchmark, and pull request comments one shared
definition of what counts as a finding.
"""

from __future__ import annotations

import re
from typing import Any

SEVERITIES = {"critical", "high", "medium", "low"}
CONFIDENCES = {"high", "medium", "low"}
MAX_RUNTIME_RISKS_PER_FUNCTION = 2

_DESCRIPTION_FIELDS = ("description", "message", "title", "reason", "issue", "fix", "text", "details", "summary")
_LABEL_FIELDS = ("type", "category", "severity", "level", "kind", "symbol")
_FIX_CODE_FIELDS = ("fix_code", "fixed_code", "replacement_code", "updated_code", "code", "snippet")
_TYPE_VALIDATION_MARKERS = (
    "missing type validation",
    "missing input validation",
    "add input validation",
    "type check",
)

_RUNTIME_TEXT_PATTERN = re.compile(
    r"runtime|exception|null|none|undefined|indexerror|keyerror|zerodivision|typeerror|valueerror"
    r"|overflow|crash|memory|out of bounds",
    re.IGNORECASE,
)
_TYPED_PARAMS_PATTERN = re.compile(r"def\s+[A-Za-z_]\w*\s*\([^)]*:\s*[^)]*\)\s*(?:->\s*[^:]+)?:")
_PYTHON_PATTERN = re.compile(r"\bdef\s+[A-Za-z_]\w*\s*\(|\bimport\s+[A-Za-z_]")
_SPECIFIC_OVERFLOW_PATTERN = re.compile(
    r"32-bit|64-bit|fixed[-\s]?width|integer limit|c\+\+|cpp|javascript|typed array",
    re.IGNORECASE,
)
_FUNCTION_NAME_PATTERNS = (
    re.compile(r"\b(?:function|method|def)\s+([A-Za-z_]\w*)\b", re.IGNORECASE),
    re.compile(r"\bin\s+([A-Za-z_]\w*)\s*\(", re.IGNORECASE),
    re.compile(r"\bfor\s+([A-Za-z_]\w*)\s*\(", re.IGNORECASE),
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def entry_description(entry: Any) -> str:
    """Return the human-readable text of a bug, fix, or suggestion entry."""
    if isinstance(entry, str):
        return _clean(entry)
    if not isinstance(entry, dict):
        return ""
    for field in _DESCRIPTION_FIELDS:
        text = _clean(entry.get(field))
        if text:
            return text
    return ""


def entry_line(entry: Any) -> int | None:
    """Return the entry's 1-based line number, if it has a usable one."""
    if not isinstance(entry, dict):
        return None
    raw_line = entry.get("line")
    location = entry.get("location")
    if raw_line is None and isinstance(location, dict):
        # Some models nest the position: {"location": {"file": ..., "line": 3}}.
        raw_line = location.get("line")
    try:
        line = int(raw_line)
    except (TypeError, ValueError):
        return None
    return line if line > 0 else None


def _label(entry: Any, fallback: str) -> str:
    if isinstance(entry, dict):
        for field in _LABEL_FIELDS:
            text = _clean(entry.get(field))
            if text:
                return text.upper()
    return fallback


def _pick(value: Any, allowed: set[str], default: str) -> str:
    text = _clean(value).lower()
    return text if text in allowed else default


def _fix_code(entry: Any) -> str:
    if isinstance(entry, dict):
        for field in _FIX_CODE_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _dedupe_key(label: str, description: str) -> str:
    text = re.sub(r"\bdocstrings\b", "docstring", f"{label} {description}", flags=re.IGNORECASE).lower()
    return " ".join(re.sub(r"[^a-z0-9\s]", "", text).split())


def _merge(entries: list[Any], kind: str, typed_params: bool) -> list[dict[str, Any]]:
    """Normalize entries and collapse duplicates, counting how often each appeared."""
    merged: dict[str, dict[str, Any]] = {}
    for entry in entries:
        description = entry_description(entry)
        if not description:
            continue
        # Type hints already document parameter types, so "add type validation" is noise.
        if typed_params and any(marker in description.lower() for marker in _TYPE_VALIDATION_MARKERS):
            continue

        label = _label(entry, kind.upper())
        key = _dedupe_key(label, description)
        if not key:
            continue

        code = _fix_code(entry) if kind == "fix" else ""
        if key in merged:
            merged[key]["count"] += 1
            if code and not merged[key]["code"]:
                merged[key]["code"] = code
            continue

        details = entry if isinstance(entry, dict) else {}
        merged[key] = {
            "label": label,
            "description": description,
            "severity": _pick(details.get("severity") or details.get("priority"), SEVERITIES, "low"),
            "confidence": _pick(details.get("confidence"), CONFIDENCES, "medium"),
            "file": _clean(details.get("file") or details.get("path")),
            "line": entry_line(details),
            "category": _clean(details.get("category")).lower(),
            "source": _clean(details.get("source")).lower(),
            "code": code,
            "count": 1,
        }
    return list(merged.values())


def _is_runtime_risk(finding: dict[str, Any]) -> bool:
    return finding["category"] == "runtime" or bool(_RUNTIME_TEXT_PATTERN.search(finding["description"]))


def _is_generic_overflow_warning(description: str, source_code: str) -> bool:
    """Python ints never overflow, so drop overflow warnings that name no fixed-width context."""
    if "overflow" not in description.lower() or not _PYTHON_PATTERN.search(source_code):
        return False
    return not _SPECIFIC_OVERFLOW_PATTERN.search(description)


def _function_name(description: str) -> str:
    for pattern in _FUNCTION_NAME_PATTERNS:
        match = pattern.search(description)
        if match:
            return match.group(1)
    return "global"


def _limit_per_function(findings: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep at most `limit` runtime risks per function so one hotspot can't flood the list."""
    counts: dict[tuple[str, str], int] = {}
    kept: list[dict[str, Any]] = []
    for finding in findings:
        key = (finding["file"], _function_name(finding["description"]))
        if counts.get(key, 0) >= limit:
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(finding)
    return kept


def build_findings(files: list[dict[str, Any]], source_code: str = "") -> dict[str, list[dict[str, Any]]]:
    """Merge per-file review entries into the four lists the UI displays."""
    source_code = source_code or ""
    typed_params = bool(_TYPED_PARAMS_PATTERN.search(source_code))

    def collect(field: str) -> list[Any]:
        return [entry for file_result in files for entry in (file_result.get(field) or [])]

    bugs = _merge(collect("bugs"), "bug", typed_params)
    runtime_candidates = [finding for finding in bugs if _is_runtime_risk(finding)]
    runtime_risks = _limit_per_function(
        [
            finding
            for finding in runtime_candidates
            if not _is_generic_overflow_warning(finding["description"], source_code)
        ],
        MAX_RUNTIME_RISKS_PER_FUNCTION,
    )

    return {
        "runtime_risks": runtime_risks,
        "code_issues": [finding for finding in bugs if not _is_runtime_risk(finding)],
        "fixes": [fix for fix in _merge(collect("fixes"), "fix", typed_params) if fix["code"]],
        "suggestions": _merge(collect("suggestions"), "suggestion", typed_params),
    }
