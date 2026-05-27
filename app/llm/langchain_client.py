"""LangChain LLM client for Code Review Copilot using Groq Mixtral."""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

from langchain_groq import ChatGroq
from langchain.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()
def _escape_for_prompt(text: str) -> str:
    # Escape ALL curly braces safely for LangChain templates
      return text.replace("{", "{{").replace("}", "}}")

DEFAULT_GROQ_MODELS = [
    "llama-3.3-8b-instant",
    "llama-3.3-70b-versatile",
]


class LLMResponseParseError(Exception):
    """Raised when LLM output cannot be parsed into required JSON schema."""


class ReviewScore(BaseModel):
    """Score model with bounded rubric fields."""

    readability: int = Field(ge=0, le=2)
    correctness: int = Field(ge=0, le=3)
    efficiency: int = Field(ge=0, le=2)
    best_practices: int = Field(ge=0, le=2)
    maintainability: int = Field(ge=0, le=1)
    final_score: int = Field(ge=0, le=10)

    @field_validator("final_score")
    @classmethod
    def validate_final_score(cls, value: int, info: Any) -> int:
        data = info.data
        expected = (
            data.get("readability", 0)
            + data.get("correctness", 0)
            + data.get("efficiency", 0)
            + data.get("best_practices", 0)
            + data.get("maintainability", 0)
        )
        return min(max(value, 0), 10) if value != expected else value


class CodeReviewResult(BaseModel):
    """Strict JSON contract for code review output."""

    bugs: list[Any]
    fixes: list[Any]
    suggestions: list[Any]
    complexity: str
    score: ReviewScore
    reasoning: str


class _CodePatterns(BaseModel):
    """AST-derived code patterns used to suppress irrelevant findings."""

    parsed: bool = False
    has_division: bool = False
    uses_input: bool = False
    has_unsafe_operations: bool = False
    functions_total: int = 0
    functions_missing_return: int = 0


def _schema_example() -> str:
    return (
        '{\n'
        '  "bugs": [],\n'
        '  "fixes": [],\n'
        '  "suggestions": [],\n'
        '  "complexity": "",\n'
        '  "score": {\n'
        '    "readability": 0,\n'
        '    "correctness": 0,\n'
        '    "efficiency": 0,\n'
        '    "best_practices": 0,\n'
        '    "maintainability": 0,\n'
        '    "final_score": 0\n'
        "  },\n"
        '  "reasoning": ""\n'
        "}"
    )


def _format_static_analysis(pylint_output: dict[str, Any] | str) -> str:
    """Convert static analysis payload into readable prompt text.

    Expected dict shape:
    {
      "errors": [...],
      "warnings": [...],
      "score": float
    }
    """
    if isinstance(pylint_output, str):
        return pylint_output.strip() or "No static analysis data available."

    if not isinstance(pylint_output, dict):
        return "No static analysis data available."

    errors = pylint_output.get("errors", []) or []
    warnings = pylint_output.get("warnings", []) or []
    score = pylint_output.get("score", "N/A")

    lines: list[str] = []
    lines.append(f"Pylint Score: {score}/10")
    lines.append("")

    lines.append("Errors:")
    if not errors:
        lines.append("- None")
    else:
        for index, item in enumerate(errors, start=1):
            if isinstance(item, dict):
                line = item.get("line", 0)
                message = item.get("message", "Unknown error")
                path = item.get("path", "unknown")
                symbol = item.get("symbol", "")
            else:
                line = 0
                message = str(item)
                path = "unknown"
                symbol = ""
            symbol_part = f" [{symbol}]" if symbol else ""
            lines.append(f"{index}. line {line} | {path}{symbol_part} - {message}")

    lines.append("")
    lines.append("Warnings:")
    if not warnings:
        lines.append("- None")
    else:
        for index, item in enumerate(warnings, start=1):
            if isinstance(item, dict):
                line = item.get("line", 0)
                message = item.get("message", "Unknown warning")
                path = item.get("path", "unknown")
                symbol = item.get("symbol", "")
            else:
                line = 0
                message = str(item)
                path = "unknown"
                symbol = ""
            symbol_part = f" [{symbol}]" if symbol else ""
            lines.append(f"{index}. line {line} | {path}{symbol_part} - {message}")

    return "\n".join(lines)


def _detect_language(source_code: str) -> str:
    """Best-effort language detection for prompt grounding."""
    code = source_code.lower()
    if "def " in code or "import " in code or "lambda " in code or "print(" in code:
        return "Python"
    if "function " in code or "console.log(" in code or "=>" in code or "var " in code:
        return "JavaScript"
    if "#include" in code or "std::" in code or "int main(" in code:
        return "C++"
    return "Unknown"


def _build_prompt(source_code: str, pylint_output: dict[str, Any] | str) -> list[tuple[str, str]]:
    static_section = _format_static_analysis(pylint_output)

    # Escape schema braces (already correct)
    escaped_schema = _schema_example().replace("{", "{{").replace("}", "}}")

    detected_language = _detect_language(source_code)

    # ✅ FIX: escape user inputs BEFORE using in f-string
    

    safe_code = _escape_for_prompt(source_code[:20000])
    safe_static = _escape_for_prompt(static_section[:12000])

    user_prompt = (
        "Analyze the code and static analysis output.\n"
        f"Detected language: {detected_language}\n"
        "Use language-aware reasoning and align suggestions with real behavior of this language.\n"
        "Tasks:\n"
        "1) Identify bugs (syntax, logical, runtime risks)\n"
        "2) Suggest fixes with improved code\n"
        "3) Review code quality (readability, naming, best practices)\n"
        "4) Suggest optimizations\n"
        "5) Analyze time and space complexity\n\n"
        "Focus on concrete issues visible in the provided code, including:\n"
        "- missing validation\n"
        "- edge cases\n"
        "- incorrect logic\n\n"
        "Do not suggest issues that are not relevant to the given programming language.\n"
        "Avoid generic assumptions and unsupported claims.\n\n"
        "Validation rule:\n"
        "- If function parameters already have type hints, do NOT flag missing validation.\n"
        "- Only flag validation issues when unsafe operations are present.\n\n"
        "Fix quality rules:\n"
        "- Return FULL corrected code for fixes\n"
        "- Keep fixes minimal and realistic\n\n"
        "Return STRICT JSON only.\n"
        f"JSON schema example:\n{escaped_schema}\n\n"
        f"CODE:\n{safe_code}\n\n"
        f"STATIC_ANALYSIS:\n{safe_static}"
    )

    return [
        (
            "system",
            "You are a senior software engineer and code reviewer. "
            "Always return valid JSON matching the requested schema.",
        ),
        ("human", user_prompt),
    ]

def extract_json(text: str) -> str:
    """Extract JSON string from noisy LLM output.

    - Removes markdown code fences (```json ... ```, ``` ... ```)
    - Extracts substring from first '{' to last '}'
    - Strips surrounding whitespace
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("```json", "").replace("```JSON", "").replace("```", "")

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or first >= last:
        raise LLMResponseParseError("No JSON object found in model response.")

    return cleaned[first : last + 1].strip()


def _sanitize_json_text(raw_json: str) -> str:
    """Best-effort cleanup for common LLM JSON glitches."""
    
    text = str(raw_json or "").strip()

    # Remove triple quotes
    text = re.sub(r'""".*?"""', '""', text, flags=re.DOTALL)

    # Fix smart quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")

    # Remove trailing commas
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    return text

def _normalize_payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize parsed payload into required schema before validation."""
    bugs = payload.get("bugs", [])
    fixes = payload.get("fixes", [])
    suggestions = payload.get("suggestions", [])
    complexity = payload.get("complexity", "N/A")
    reasoning = payload.get("reasoning", "")
    score_raw = payload.get("score", {}) if isinstance(payload.get("score", {}), dict) else {}

    def _to_bounded_int(value: Any, low: int, high: int) -> int:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            parsed = 0
        return min(max(parsed, low), high)

    readability = _to_bounded_int(score_raw.get("readability", 0), 0, 2)
    correctness = _to_bounded_int(score_raw.get("correctness", 0), 0, 3)
    efficiency = _to_bounded_int(score_raw.get("efficiency", 0), 0, 2)
    best_practices = _to_bounded_int(score_raw.get("best_practices", 0), 0, 2)
    maintainability = _to_bounded_int(score_raw.get("maintainability", 0), 0, 1)
    final_score = readability + correctness + efficiency + best_practices + maintainability

    return {
        "bugs": bugs if isinstance(bugs, list) else [str(bugs)],
        "fixes": fixes if isinstance(fixes, list) else [str(fixes)],
        "suggestions": suggestions if isinstance(suggestions, list) else [str(suggestions)],
        "complexity": str(complexity),
        "score": {
            "readability": readability,
            "correctness": correctness,
            "efficiency": efficiency,
            "best_practices": best_practices,
            "maintainability": maintainability,
            "final_score": final_score,
        },
        "reasoning": str(reasoning),
    }


def _entry_text(entry: Any) -> str:
    """Extract human-readable text from a bug/fix/suggestion entry."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for field in ("description", "message", "title", "reason", "text", "details"):
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            return json.dumps(entry, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(entry)
    return str(entry)


def _is_python_code(source_code: str) -> bool:
    code = source_code.lower()
    return "def " in code or "import " in code or "lambda " in code


def _looks_like_unrealistic_python_overflow_fix(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    if "overflow" not in text:
        return False
    # Keep fixes only when they clearly target fixed-width integers or specialized numeric contexts.
    allow_context = (
        "numpy",
        "int32",
        "int64",
        "fixed-width",
        "typed array",
        "c extension",
    )
    return not any(token in text for token in allow_context)


def _is_optional_best_practice(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    optional_markers = (
        "docstring",
        "style",
        "readability",
        "naming",
        "comment",
        "best practice",
        "refactor",
        "formatting",
    )
    return any(marker in text for marker in optional_markers)


def _is_optional_fix(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    optional_markers = (
        "input validation",
        "type validation",
        "type check",
        "docstring",
        "readability",
        "style",
        "naming",
        "comment",
        "best practice",
        "refactor",
    )
    return any(marker in text for marker in optional_markers)


def _has_python_param_type_hints(source_code: str) -> bool:
    """Detect Python function parameter annotations like `a: int`."""
    code = str(source_code or "")
    return bool(re.search(r"def\s+\w+\s*\([^)]*:\s*[^)]*\)\s*(->\s*[^:\n]+)?\s*:", code))


def _analyze_code_patterns(source_code: str) -> _CodePatterns:
    """Analyze source with Python AST to detect relevant code patterns."""
    if not _is_python_code(source_code):
        return _CodePatterns(parsed=False)

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _CodePatterns(parsed=False)

    has_division = False
    uses_input = False
    has_unsafe_operations = False
    function_defs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    missing_return_count = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            has_division = True
            has_unsafe_operations = True

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "input":
                uses_input = True

            # Calls such as items.pop(...) are potentially unsafe for missing-key/index handling.
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"pop"}:
                has_unsafe_operations = True

        if isinstance(node, ast.Subscript):
            has_unsafe_operations = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_defs.append(node)

    for fn in function_defs:
        has_return = any(isinstance(n, ast.Return) for n in ast.walk(fn))
        if not has_return:
            missing_return_count += 1

    return _CodePatterns(
        parsed=True,
        has_division=has_division,
        uses_input=uses_input,
        has_unsafe_operations=has_unsafe_operations,
        functions_total=len(function_defs),
        functions_missing_return=missing_return_count,
    )


def _is_validation_issue(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    markers = (
        "missing input validation",
        "missing type validation",
        "type check",
        "validate input",
        "input validation",
    )
    return any(marker in text for marker in markers)


def _is_division_issue(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    markers = (
        "division by zero",
        "divide by zero",
        "zerodivision",
        "division check",
        "check denominator",
        "denominator",
    )
    return any(marker in text for marker in markers)


def _is_missing_return_issue(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    markers = (
        "missing return",
        "no return",
        "function lacks return",
        "add return",
    )
    return any(marker in text for marker in markers)


def _is_low_value_suggestion(entry: Any) -> bool:
    text = _entry_text(entry).lower()
    low_value_markers = (
        "add newline",
        "trailing newline",
        "module docstring",
        "file docstring",
        "formatting only",
    )
    return any(marker in text for marker in low_value_markers)


def _non_empty_lines(source_code: str) -> int:
    return len([line for line in str(source_code or "").splitlines() if line.strip()])


def _python_function_count(source_code: str) -> int:
    return len(re.findall(r"^\s*def\s+\w+\s*\(", str(source_code or ""), flags=re.MULTILINE))


def _small_snippet_suggestion_limit(source_code: str) -> int:
    lines = _non_empty_lines(source_code)
    fn_count = _python_function_count(source_code)
    if lines <= 15 and fn_count <= 1:
        return 1
    if lines <= 40 and fn_count <= 1:
        return 2
    return 4


def _is_missing_python_docstring(source_code: str) -> bool:
    code = str(source_code or "")
    match = re.search(
        r"^\s*def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:\n]+)?\s*:\s*\n(?P<body>(?:[ \t]+.*\n?)*)",
        code,
        flags=re.MULTILINE,
    )
    if not match:
        return False
    body_lines = [line.strip() for line in match.group("body").splitlines() if line.strip()]
    if not body_lines:
        return False
    first = body_lines[0]
    return not (first.startswith('"""') or first.startswith("'''"))


def _is_docstring_suggestion(entry: Any) -> bool:
    return "docstring" in _entry_text(entry).lower()


def _prioritize_suggestions(suggestions: list[Any], source_code: str) -> list[Any]:
    filtered = [item for item in suggestions if not _is_low_value_suggestion(item)]
    missing_docstring = _is_missing_python_docstring(source_code)

    def _rank(item: Any) -> tuple[int, int]:
        text = _entry_text(item).lower()
        if missing_docstring and _is_docstring_suggestion(item):
            return (0, len(text))
        if _is_optional_best_practice(item):
            return (2, len(text))
        return (1, len(text))

    ranked = sorted(filtered, key=_rank)
    limit = _small_snippet_suggestion_limit(source_code)
    return ranked[:limit]


def _pylint_counts(pylint_output: dict[str, Any] | str) -> tuple[int, int]:
    if not isinstance(pylint_output, dict):
        return 0, 0
    errors = pylint_output.get("errors", []) or []
    warnings = pylint_output.get("warnings", []) or []
    return len(errors), len(warnings)


def _post_process_payload(
    payload: dict[str, Any],
    source_code: str,
    pylint_output: dict[str, Any] | str,
) -> dict[str, Any]:
    """Apply pragmatic post-processing to keep fixes realistic and minimal."""
    bugs = payload.get("bugs", [])
    fixes = payload.get("fixes", [])
    suggestions = payload.get("suggestions", [])
    patterns = _analyze_code_patterns(source_code)

    # Remove unrealistic Python overflow fixes.
    if _is_python_code(source_code):
        fixes = [fix for fix in fixes if not _looks_like_unrealistic_python_overflow_fix(fix)]

    has_type_hints = _has_python_param_type_hints(source_code)
    has_unsafe_ops = patterns.has_unsafe_operations
    if has_type_hints and not has_unsafe_ops:
        # Type hints already provide contract clarity; avoid noisy validation findings.
        bugs = [bug for bug in bugs if not _is_validation_issue(bug)]
        suggestions = [suggestion for suggestion in suggestions if not _is_validation_issue(suggestion)]
        fixes = [fix for fix in fixes if not _is_validation_issue(fix)]
    elif not patterns.uses_input and not has_unsafe_ops:
        # Input validation suggestions are irrelevant when code does not read user input
        # and there is no obvious unsafe operation.
        bugs = [bug for bug in bugs if not _is_validation_issue(bug)]
        suggestions = [suggestion for suggestion in suggestions if not _is_validation_issue(suggestion)]
        fixes = [fix for fix in fixes if not _is_validation_issue(fix)]

    # Division-related findings are relevant only when division/modulo operations exist.
    if patterns.parsed and not patterns.has_division:
        bugs = [bug for bug in bugs if not _is_division_issue(bug)]
        suggestions = [suggestion for suggestion in suggestions if not _is_division_issue(suggestion)]
        fixes = [fix for fix in fixes if not _is_division_issue(fix)]

    # Missing-return findings are relevant only when at least one function has no return.
    if patterns.parsed and patterns.functions_total > 0 and patterns.functions_missing_return == 0:
        bugs = [bug for bug in bugs if not _is_missing_return_issue(bug)]
        suggestions = [suggestion for suggestion in suggestions if not _is_missing_return_issue(suggestion)]
        fixes = [fix for fix in fixes if not _is_missing_return_issue(fix)]

    error_count, warning_count = _pylint_counts(pylint_output)
    has_real_bugs = len(bugs) > 0 or error_count > 0
    only_optional_suggestions = all(_is_optional_best_practice(item) for item in suggestions) if suggestions else True

    # Keep fixes high-signal only when there are no real bugs.
    # When bugs exist, fixes like "add input validation" can be the correct remediation
    # (e.g., preventing division-by-zero), so we must not discard them.
    if not has_real_bugs:
        fixes = [fix for fix in fixes if not _is_optional_fix(fix)]

    # If code appears clean, avoid generating unnecessary fixes.
    if not has_real_bugs and error_count == 0 and warning_count <= 1 and only_optional_suggestions:
        fixes = []
    if not has_real_bugs:
        fixes = []

    suggestions = _prioritize_suggestions(suggestions, source_code)

    payload["bugs"] = bugs
    payload["fixes"] = fixes
    payload["suggestions"] = suggestions
    return payload


def _extract_json_payload(text: str) -> dict[str, Any]:
    cleaned_json = extract_json(text)
    cleaned_json = _sanitize_json_text(cleaned_json)
    logger.info("Cleaned JSON for parsing: %s", cleaned_json)

    try:
        parsed = json.loads(cleaned_json)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError("Invalid JSON in model response.") from exc

    if not isinstance(parsed, dict):
        raise LLMResponseParseError("Model response JSON must be an object.")
    return _normalize_payload_shape(parsed)


def _normalize_scores(result: CodeReviewResult) -> dict[str, Any]:
    payload = result.model_dump()
    score = payload["score"]
    computed_final = (
        score["readability"]
        + score["correctness"]
        + score["efficiency"]
        + score["best_practices"]
        + score["maintainability"]
    )
    score["final_score"] = min(max(computed_final, 0), 10)
    return payload


def _build_repair_prompt(invalid_output: str) -> list[tuple[str, str]]:
    safe_invalid = _escape_for_prompt(invalid_output[:12000])

    repair = (
        "Your previous response was not valid JSON for the required schema.\n"
        "Fix it and return only valid JSON.\n"
        "Return ONLY valid JSON. Do not include explanations, markdown, or extra text.\n"
        "Return ONLY JSON. No explanation.\n"
        f"Required schema:\n{_schema_example()}\n\n"
        f"Previous response:\n{safe_invalid}"
    )

    return [
        ("system", "Return only valid JSON matching the schema."),
        ("human", repair),
    ]

def _build_fix_only_prompt(source_code: str, issues: list[Any]) -> list[tuple[str, str]]:
    """Ask the model for a single concrete fix object only."""
    fix_prompt = (
        "You are a strict code fixer.\n\n"
        "Given the issue(s) and code, return ONLY a valid fix.\n\n"
        "RULES:\n"
        "- Must return JSON\n"
        "- Must include:\n"
        '  {\n    "description": "...",\n    "code": "FULL corrected code"\n  }\n'
        "- No explanation outside JSON\n\n"
        f"CODE:\n{source_code[:20000]}\n\n"
        f"ISSUES:\n{json.dumps(issues, ensure_ascii=False)[:12000]}"
    )
    return [
        ("system", "Return only JSON for a single fix object."),
        ("human", fix_prompt),
    ]


def _try_parse_fix_object(raw_text: str) -> dict[str, str] | None:
    """Parse a single fix object {'description': str, 'code': str} from model output."""
    try:
        payload = _extract_json_payload(raw_text)
    except Exception:
        return None

    # _extract_json_payload normalizes into the main schema; here we need a single object.
    # Fallback: parse raw JSON object directly.
    if isinstance(payload, dict) and "description" in payload and "code" in payload:
        desc = payload.get("description")
        code = payload.get("code")
        if isinstance(desc, str) and desc.strip() and isinstance(code, str) and code.strip():
            return {"description": desc.strip(), "code": code.strip()}

    try:
        raw_json = extract_json(str(raw_text or ""))
        parsed = json.loads(raw_json)
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None
    desc = parsed.get("description")
    code = parsed.get("code")
    if not isinstance(desc, str) or not desc.strip():
        return None
    if not isinstance(code, str) or not code.strip():
        return None
    return {"description": desc.strip(), "code": code.strip()}


def _fallback_response() -> dict[str, Any]:
    """Safe fallback payload when parsing fails repeatedly."""
    return {
        "bugs": [],
        "fixes": [],
        "suggestions": ["LLM response parsing failed"],
        "complexity": "N/A",
        "score": {
            "readability": 0,
            "correctness": 0,
            "efficiency": 0,
            "best_practices": 0,
            "maintainability": 0,
            "final_score": 0,
        },
        "reasoning": "Parsing failure",
    }


def _build_llm(model_name: str) -> ChatGroq:
    """Create a Groq chat client for a specific model."""
    return ChatGroq(
        model=model_name,
        api_key=settings.groq_api_key,
        temperature=0.2,
    )


def _resolve_model_candidates() -> list[str]:
    """Resolve ordered model candidates for automatic fallback."""
    configured_primary = (settings.llm_model or "").strip()
    configured_fallbacks = [
        item.strip()
        for item in str(settings.llm_fallback_model or "").split(",")
        if item.strip()
    ]

    # Remap known deprecated values to current stable defaults.
    deprecated_map = {
        "mixtral-8x7b-32768": "llama-3.1-8b-instant",
        "llama3-70b-8192": "llama-3.1-70b-versatile",
    }
    if configured_primary in deprecated_map:
        logger.warning(
            "Deprecated model configured (%s). Replacing with %s.",
            configured_primary,
            deprecated_map[configured_primary],
        )
        configured_primary = deprecated_map[configured_primary]

    ordered = []
    if configured_primary:
        ordered.append(configured_primary)
    ordered.extend(configured_fallbacks)
    ordered.extend(DEFAULT_GROQ_MODELS)

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for model in ordered:
        if model not in seen:
            seen.add(model)
            deduped.append(model)

    return deduped


async def _invoke_with_model_fallback(
    prompt: ChatPromptTemplate,
    start_index: int = 0,
) -> tuple[Any, int]:
    """Invoke LLM using candidate models, auto-falling back on invocation failure."""
    candidates = _resolve_model_candidates()
    if not candidates:
        raise RuntimeError("No Groq models configured.")

    index = max(0, min(start_index, len(candidates) - 1))
    last_exc: Exception | None = None

    for model_index in range(index, len(candidates)):
        model_name = candidates[model_index]
        llm = _build_llm(model_name)
        logger.info("Invoking Groq model: %s", model_name)
        try:
            response = await (prompt | llm).ainvoke({ })
            return response, model_index
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Groq model failed: %s. Falling back to next model. Error: %s",
                model_name,
                exc,
            )
            continue

    raise RuntimeError("All configured Groq models failed.") from last_exc


async def generate_llm_review(
    source_code: str,
    pylint_output: dict[str, Any] | str,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Generate structured review insights from code and static analysis output."""
    if not settings.groq_api_key:
        return {
            "bugs": ["LLM skipped: GROQ_API_KEY not configured."],
            "fixes": [],
            "suggestions": ["Set GROQ_API_KEY to enable LLM code review."],
            "complexity": "N/A",
            "score": {
                "readability": 0,
                "correctness": 0,
                "efficiency": 0,
                "best_practices": 0,
                "maintainability": 0,
                "final_score": 0,
            },
            "reasoning": "Model was not executed because GROQ_API_KEY is missing.",
        }

    model_candidates = _resolve_model_candidates()
    logger.info("Groq model candidates: %s", model_candidates)
    active_model_index = 0

    prompt_messages = _build_prompt(source_code=source_code, pylint_output=pylint_output)
    prompt = ChatPromptTemplate.from_messages(prompt_messages)

    raw_output = ""
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            logger.info("Main generation attempt %d/%d", attempt, total_attempts)
            response, active_model_index = await _invoke_with_model_fallback(
                prompt=prompt,
                start_index=active_model_index,
            )
            print("\n========== RAW LLM RESPONSE ==========\n")
            print(response)
            print("\n=====================================\n")
            raw_output = str(response.content)
            logger.info("Raw LLM response (attempt %d): %s", attempt, raw_output)
            payload = _extract_json_payload(raw_output)
            payload = _post_process_payload(payload, source_code, pylint_output)
            validated = CodeReviewResult.model_validate(payload)
            # If the model reported issues but produced no fixes, request a single fix object.
            if validated.bugs and not validated.fixes:
                try:
                    issues = list(validated.bugs)
                    fix_only_messages = _build_fix_only_prompt(source_code, issues)
                    fix_only_prompt = ChatPromptTemplate.from_messages(fix_only_messages)
                    fix_response, active_model_index = await _invoke_with_model_fallback(
                        prompt=fix_only_prompt,
                        start_index=active_model_index,
                    )
                    fix_obj = _try_parse_fix_object(str(fix_response.content))
                    if fix_obj:
                        payload["fixes"] = [fix_obj]
                        validated = CodeReviewResult.model_validate(payload)
                except Exception:
                    # fail silently (strict mode)
                    pass
            return _normalize_scores(validated)
        except (LLMResponseParseError, ValidationError) as exc:
            logger.warning("LLM JSON parse failed on attempt %d/%d: %s", attempt, total_attempts, exc)
            if attempt == total_attempts:
                break
            repair_prompt = ChatPromptTemplate.from_messages(
                _build_repair_prompt(invalid_output=raw_output)
            )
            logger.info(
                "Invoking Groq model for repair (attempt %d/%d)",
                attempt,
                total_attempts,
            )
            response, active_model_index = await _invoke_with_model_fallback(
                prompt=repair_prompt,
                start_index=active_model_index,
            )
            print("\n========== RAW LLM RESPONSE ==========\n")
            print(response)
            print("\n=====================================\n")
            raw_output = str(response.content)
            logger.info("Raw LLM repair response (attempt %d): %s", attempt, raw_output)
            try:
                payload = _extract_json_payload(raw_output)
                payload = _post_process_payload(payload, source_code, pylint_output)
                validated = CodeReviewResult.model_validate(payload)
                if validated.bugs and not validated.fixes:
                    try:
                        issues = list(validated.bugs)
                        fix_only_messages = _build_fix_only_prompt(source_code, issues)
                        fix_only_prompt = ChatPromptTemplate.from_messages(fix_only_messages)
                        fix_response, active_model_index = await _invoke_with_model_fallback(
                            prompt=fix_only_prompt,
                            start_index=active_model_index,
                        )
                        fix_obj = _try_parse_fix_object(str(fix_response.content))
                        if fix_obj:
                            payload["fixes"] = [fix_obj]
                            validated = CodeReviewResult.model_validate(payload)
                    except Exception:
                        pass
                return _normalize_scores(validated)
            except (LLMResponseParseError, ValidationError):
                continue
        except Exception:
            logger.exception("Unexpected failure while generating LLM review.")
            if attempt == total_attempts:
                break

    logger.error("LLM completely failed. Returning safe fallback with debug.")

    return {
        "bugs": ["LLM failed to generate valid response"],
        "fixes": [],
        "suggestions": ["Try again or check model output format"],
        "complexity": "N/A",
        "score": {
            "readability": 0,
            "correctness": 0,
            "efficiency": 0,
            "best_practices": 0,
            "maintainability": 0,
            "final_score": 0,
        },
        "reasoning": raw_output[:500] if raw_output else "No response"
    }
