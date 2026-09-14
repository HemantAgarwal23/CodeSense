"""AST-driven static analyzer for CodeSense AI.

Primary API:
    analyze_code(code: str) -> dict

Backward-compatible API:
    run_static_analysis(file_path: str) -> dict
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024
SHADOWABLE_BUILTINS = {"list", "dict", "set", "str", "int", "tuple", "input", "type", "id"}


@dataclass(frozen=True)
class AnalyzerIssue:
    """Normalized issue model for deterministic output and deduplication."""

    category: str
    severity: str
    message: str
    line: int
    symbol: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.severity,
            "line": self.line,
            "symbol": self.symbol,
            "message": self.message,
            "category": self.category,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _dedupe_issues(issues: list[AnalyzerIssue]) -> list[AnalyzerIssue]:
    seen: set[tuple[str, int, str]] = set()
    unique: list[AnalyzerIssue] = []
    for issue in issues:
        key = (issue.symbol, issue.line, issue.message.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _extract_function_params(node: ast.AST) -> list[ast.arg]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    params = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
    return [p for p in params if p.arg not in {"self", "cls"}]


def _has_missing_type_hints(node: ast.AST) -> bool:
    """Return True when function parameters or return annotation are missing."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    params = _extract_function_params(node)
    if any(param.annotation is None for param in params):
        return True
    return node.returns is None


def _has_type_checks(node: ast.AST, param_names: set[str]) -> bool:
    for current in ast.walk(node):
        if not isinstance(current, ast.Call):
            continue
        if not isinstance(current.func, ast.Name) or current.func.id != "isinstance":
            continue
        if current.args and isinstance(current.args[0], ast.Name) and current.args[0].id in param_names:
            return True
    return False


def _has_basic_edge_case_handling(node: ast.AST, param_names: set[str]) -> bool:
    """Heuristic: look for if/try/assert checks involving parameters."""
    for current in ast.walk(node):
        if isinstance(current, ast.Try):
            return True
        if isinstance(current, ast.Assert):
            names = {n.id for n in ast.walk(current) if isinstance(n, ast.Name)}
            if names & param_names:
                return True
        if isinstance(current, ast.If):
            names = {n.id for n in ast.walk(current.test) if isinstance(n, ast.Name)}
            if names & param_names:
                return True
    return False


def _is_validation_guard_test(test: ast.AST, param_names: set[str]) -> bool:
    """Detect guard conditions that likely validate function parameters."""
    # e.g. if a is None:
    if isinstance(test, ast.Compare):
        names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
        has_param = bool(names & param_names)
        checks_none = any(isinstance(n, ast.Constant) and n.value is None for n in ast.walk(test))
        return has_param and checks_none

    # e.g. if not a:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Name):
        return test.operand.id in param_names

    # e.g. if not isinstance(a, int):
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Call):
        call = test.operand
        if isinstance(call.func, ast.Name) and call.func.id == "isinstance":
            return bool(call.args) and isinstance(call.args[0], ast.Name) and call.args[0].id in param_names

    return False


def _body_returns_none_without_raise(body: list[ast.stmt]) -> bool:
    has_return_none = False
    has_raise = False
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Return) and (
                node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None)
            ):
                has_return_none = True
            if isinstance(node, ast.Raise):
                has_raise = True
    return has_return_none and not has_raise


def detect_syntax_errors(code: str) -> tuple[list[AnalyzerIssue], ast.AST | None]:
    """Detect Python syntax errors and return parsed AST when valid."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [
            AnalyzerIssue(
                category="syntax",
                severity="error",
                message=exc.msg or "Syntax error.",
                line=int(exc.lineno or 0),
                symbol="syntax-error",
            )
        ], None
    return [], tree


def detect_runtime_issues(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect runtime risks from actual operations in AST (no string rules)."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []

    # Definite division by zero.
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv)):
            if isinstance(node.right, ast.Constant) and node.right.value == 0:
                issues.append(
                    AnalyzerIssue(
                        category="runtime",
                        severity="error",
                        message="Definite division by zero.",
                        line=int(getattr(node, "lineno", 0) or 0),
                        symbol="division-by-zero",
                    )
                )

    # input() usage without parsing/casting.
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        input_vars: set[str] = set()
        casted_vars: set[str] = set()
        param_names = {p.arg for p in _extract_function_params(fn)}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Name) and node.value.func.id == "input":
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            input_vars.add(target.id)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"int", "float"}:
                if node.args and isinstance(node.args[0], ast.Name):
                    casted_vars.add(node.args[0].id)
        for var_name in sorted(input_vars - casted_vars):
            issues.append(
                AnalyzerIssue(
                    category="runtime",
                    severity="medium",
                    message=f"Input variable '{var_name}' is used without explicit validation/casting.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="missing-input-validation",
                )
            )

        # Validation consistency: returning None in validation guard should be a runtime risk.
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            if not _is_validation_guard_test(node.test, param_names):
                continue
            if _body_returns_none_without_raise(node.body):
                issues.append(
                    AnalyzerIssue(
                        category="runtime",
                        severity="medium",
                        message=(
                            f"Function '{fn.name}' returns None on validation failure; "
                            "raise an explicit error instead."
                        ),
                        line=int(getattr(node, "lineno", 0) or 0),
                        symbol="returns-none-instead-of-raise",
                    )
                )

    return _dedupe_issues(issues)


def _lambda_captures_name(lambda_node: ast.Lambda, name: str) -> bool:
    """Return True if lambda body references `name` without binding it as a lambda param/default."""
    param_names = {arg.arg for arg in lambda_node.args.args}
    param_names.update(arg.arg for arg in lambda_node.args.kwonlyargs)
    if lambda_node.args.vararg:
        param_names.add(lambda_node.args.vararg.arg)
    if lambda_node.args.kwarg:
        param_names.add(lambda_node.args.kwarg.arg)
    if name in param_names:
        return False

    # Safe pattern: lambda i=i: ...
    for default in list(lambda_node.args.defaults) + [d for d in lambda_node.args.kw_defaults if d is not None]:
        if isinstance(default, ast.Name) and default.id == name:
            return False

    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(lambda_node.body))


def detect_runtime_semantic_issues(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect runtime semantic pitfalls via AST patterns with conservative guards."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []

    # 1) Potential infinite direct recursion with no visible base condition.
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        has_control_flow_guard = any(isinstance(n, (ast.If, ast.Try, ast.While, ast.For, ast.Match)) for n in fn.body)
        self_calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == fn.name
        ]
        if not self_calls or has_control_flow_guard:
            continue

        # Conservative: only flag when all returns directly recurse.
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        if not returns:
            continue
        all_returns_recursive = True
        for ret in returns:
            value = ret.value
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == fn.name
            ):
                all_returns_recursive = False
                break
        if all_returns_recursive:
            issues.append(
                AnalyzerIssue(
                    category="runtime",
                    severity="error",
                    message="Potential infinite recursion may cause RecursionError.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="infinite-recursion-risk",
                )
            )

    # 2) Late-binding lambda capture inside loops.
    for loop in [n for n in ast.walk(tree) if isinstance(n, ast.For)]:
        if not isinstance(loop.target, ast.Name):
            continue
        loop_var = loop.target.id

        for node in ast.walk(loop):
            if not isinstance(node, ast.Lambda):
                continue
            if not _lambda_captures_name(node, loop_var):
                continue

            # Reduce false positives: focus on lambdas stored for later use.
            parent_like_store = False
            for use_node in ast.walk(loop):
                if isinstance(use_node, ast.Call) and isinstance(use_node.func, ast.Attribute):
                    if use_node.func.attr in {"append", "extend", "add"} and node in use_node.args:
                        parent_like_store = True
                        break
                if isinstance(use_node, ast.Assign):
                    if use_node.value is node:
                        parent_like_store = True
                        break

            if not parent_like_store:
                continue

            issues.append(
                AnalyzerIssue(
                    category="runtime",
                    severity="warning",
                    message="Lambda captures loop variable; closures may reference final value.",
                    line=int(getattr(node, "lineno", 0) or 0),
                    symbol="late-binding-lambda",
                )
            )

    return _dedupe_issues(issues)


def detect_exception_handling_smells(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect broad exception handling patterns that can hide runtime failures."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            # bare except:
            if handler.type is None:
                issues.append(
                    AnalyzerIssue(
                        category="runtime",
                        severity="warning",
                        message="Broad exception handling may hide runtime failures.",
                        line=int(getattr(handler, "lineno", 0) or 0),
                        symbol="broad-except",
                    )
                )
                continue

            # except Exception: pass
            is_exception_handler = isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
            handler_body_is_pass = bool(handler.body) and all(isinstance(stmt, ast.Pass) for stmt in handler.body)
            if is_exception_handler and handler_body_is_pass:
                issues.append(
                    AnalyzerIssue(
                        category="runtime",
                        severity="warning",
                        message="Broad exception handling may hide runtime failures.",
                        line=int(getattr(handler, "lineno", 0) or 0),
                        symbol="exception-pass-swallow",
                    )
                )

    return _dedupe_issues(issues)


def _is_sql_execute_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"}


def _is_tainted_sql_arg(arg: ast.AST) -> bool:
    # f"...{user_input}..."
    if isinstance(arg, ast.JoinedStr):
        return True
    # "... " + user_input
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        return True
    # "SELECT ... %s" % user_input
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
        return True
    return False


def detect_security_issues(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect obvious high-signal security vulnerabilities."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_sql_execute_call(node) and node.args:
            if _is_tainted_sql_arg(node.args[0]):
                issues.append(
                    AnalyzerIssue(
                        category="security",
                        severity="error",
                        message="Potential SQL injection: dynamic SQL passed to execute().",
                        line=int(getattr(node, "lineno", 0) or 0),
                        symbol="sql-injection-risk",
                    )
                )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            issues.append(
                AnalyzerIssue(
                    category="security",
                    severity="error",
                    message=f"Unsafe use of {node.func.id}() may execute untrusted code.",
                    line=int(getattr(node, "lineno", 0) or 0),
                    symbol="unsafe-dynamic-exec",
                )
            )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"run", "Popen", "call"} and node.args:
                for kw in node.keywords or []:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        issues.append(
                            AnalyzerIssue(
                                category="security",
                                severity="error",
                                message="Unsafe subprocess usage: shell=True detected.",
                                line=int(getattr(node, "lineno", 0) or 0),
                                symbol="unsafe-subprocess-shell-true",
                            )
                        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            value = node.value.value
            if len(value) < 8:
                continue
            lowered_value = value.lower()
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id.lower()
                if any(token in name for token in ("password", "passwd", "secret", "api_key", "token", "key")):
                    if any(token in lowered_value for token in ("changeme", "example", "test", "dummy")):
                        continue
                    issues.append(
                        AnalyzerIssue(
                            category="security",
                            severity="error",
                            message=f"Possible hardcoded secret in variable '{target.id}'.",
                            line=int(getattr(node, "lineno", 0) or 0),
                            symbol="hardcoded-secret",
                        )
                    )
                    break

    return _dedupe_issues(issues)


def detect_resource_leaks(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect obvious resource handling leaks."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        assigned_conn_names: set[str] = set()
        closed_conn_names: set[str] = set()

        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Attribute) and node.value.func.attr in {"connect", "cursor"}:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assigned_conn_names.add(target.id)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "close" and isinstance(node.func.value, ast.Name):
                    closed_conn_names.add(node.func.value.id)

        for conn_name in sorted(assigned_conn_names - closed_conn_names):
            issues.append(
                AnalyzerIssue(
                    category="runtime",
                    severity="warning",
                    message=f"Resource '{conn_name}' appears opened but never closed.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="resource-leak-risk",
                )
            )

    return _dedupe_issues(issues)


def detect_semantic_issues(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect semantic Python bugs that often pass syntax checks."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []

    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        all_defaults = list(fn.args.defaults) + [d for d in fn.args.kw_defaults if d is not None]
        for default in all_defaults:
            is_mutable_literal = isinstance(default, (ast.List, ast.Dict, ast.Set))
            is_mutable_ctor = (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id in {"list", "dict", "set"}
                and not default.args
                and not default.keywords
            )
            if is_mutable_literal or is_mutable_ctor:
                issues.append(
                    AnalyzerIssue(
                        category="semantic",
                        severity="error",
                        message="Mutable default argument may cause shared state across calls.",
                        line=int(getattr(default, "lineno", getattr(fn, "lineno", 0)) or 0),
                        symbol="mutable-default-argument",
                    )
                )
                break

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue

        for target in targets:
            if isinstance(target, ast.Name) and target.id in SHADOWABLE_BUILTINS:
                issues.append(
                    AnalyzerIssue(
                        category="semantic",
                        severity="warning",
                        message=f"Builtin '{target.id}' is shadowed by variable assignment.",
                        line=int(getattr(target, "lineno", 0) or 0),
                        symbol="builtin-shadowing",
                    )
                )

    return _dedupe_issues(issues)


def detect_logical_issues(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect logical return-flow problems via AST structure."""
    if tree is None:
        return []

    def _returns_on_all_paths(statements: list[ast.stmt]) -> bool:
        """Conservative control-flow check for guaranteed return/raise."""
        for stmt in statements:
            # Direct terminators
            if isinstance(stmt, (ast.Return, ast.Raise)):
                return True

            # if/else must both terminate
            if isinstance(stmt, ast.If):
                then_returns = _returns_on_all_paths(stmt.body)
                else_returns = _returns_on_all_paths(stmt.orelse) if stmt.orelse else False
                if then_returns and else_returns:
                    return True
                continue

            # try: if finally terminates, function terminates.
            # Otherwise, require try + all except blocks to terminate.
            if isinstance(stmt, ast.Try):
                if _returns_on_all_paths(stmt.finalbody):
                    return True
                try_returns = _returns_on_all_paths(stmt.body)
                handlers_return = bool(stmt.handlers) and all(
                    _returns_on_all_paths(handler.body) for handler in stmt.handlers
                )
                if try_returns and handlers_return:
                    return True
                continue

        return False

    def _collect_unreachable_line_numbers(statements: list[ast.stmt]) -> list[int]:
        unreachable: list[int] = []
        terminated = False
        for stmt in statements:
            if terminated:
                unreachable.append(int(getattr(stmt, "lineno", 0) or 0))
                continue
            if isinstance(stmt, (ast.Return, ast.Raise)):
                terminated = True
            elif isinstance(stmt, ast.If):
                unreachable.extend(_collect_unreachable_line_numbers(stmt.body))
                unreachable.extend(_collect_unreachable_line_numbers(stmt.orelse))
            elif isinstance(stmt, ast.Try):
                unreachable.extend(_collect_unreachable_line_numbers(stmt.body))
                unreachable.extend(_collect_unreachable_line_numbers(stmt.orelse))
                for handler in stmt.handlers:
                    unreachable.extend(_collect_unreachable_line_numbers(handler.body))
                unreachable.extend(_collect_unreachable_line_numbers(stmt.finalbody))
        return unreachable

    issues: list[AnalyzerIssue] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        has_return = bool(returns)
        has_valued_return = any(r.value is not None for r in returns)
        has_empty_return = any(r.value is None for r in returns)
        has_computation = any(isinstance(n, (ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp)) for n in ast.walk(fn))

        if has_computation and not has_return:
            issues.append(
                AnalyzerIssue(
                    category="logic",
                    severity="error",
                    message=f"Function '{fn.name}' performs computation but never returns a value.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="missing-return",
                )
            )

        if has_valued_return and has_empty_return:
            issues.append(
                AnalyzerIssue(
                    category="logic",
                    severity="warning",
                    message=f"Function '{fn.name}' has inconsistent return statements.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="inconsistent-return",
                )
            )
        elif has_valued_return and not _returns_on_all_paths(fn.body):
            issues.append(
                AnalyzerIssue(
                    category="logic",
                    severity="warning",
                    message=f"Function '{fn.name}' may exit without returning a value on some paths.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="inconsistent-return-path",
                )
            )

        unreachable_lines = [line for line in _collect_unreachable_line_numbers(fn.body) if line > 0]
        if unreachable_lines:
            issues.append(
                AnalyzerIssue(
                    category="logic",
                    severity="warning",
                    message=f"Function '{fn.name}' contains unreachable code after return/raise.",
                    line=unreachable_lines[0],
                    symbol="unreachable-code",
                )
            )

    return _dedupe_issues(issues)


def detect_best_practices(tree: ast.AST | None) -> list[AnalyzerIssue]:
    """Detect best-practice gaps using AST-only heuristics."""
    if tree is None:
        return []

    issues: list[AnalyzerIssue] = []

    # Module docstring
    if ast.get_docstring(tree) is None:
        issues.append(
            AnalyzerIssue(
                category="best_practice",
                severity="info",
                message="Add a module docstring describing this code snippet.",
                line=1,
                symbol="missing-module-docstring",
            )
        )

    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        param_names = {p.arg for p in _extract_function_params(fn)}

        # Function docstring
        if ast.get_docstring(fn) is None:
            issues.append(
                AnalyzerIssue(
                    category="best_practice",
                    severity="info",
                    message=f"Add a docstring to describe function '{fn.name}' purpose.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="missing-function-docstring",
                )
            )

        # Missing input/type validation heuristic (relevant only for parameterized functions).
        if param_names and not _has_type_checks(fn, param_names):
            issues.append(
                AnalyzerIssue(
                    category="best_practice",
                    severity="info",
                    message=f"Add input validation using isinstance in function '{fn.name}'.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="missing-input-type-validation",
                )
            )

        # Type-safety heuristic.
        if _has_missing_type_hints(fn):
            issues.append(
                AnalyzerIssue(
                    category="best_practice",
                    severity="info",
                    message=f"Add type hints for parameters/return in function '{fn.name}'.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="missing-type-hints",
                )
            )

        # Edge case handling heuristic.
        if param_names and not _has_basic_edge_case_handling(fn, param_names):
            issues.append(
                AnalyzerIssue(
                    category="best_practice",
                    severity="info",
                    message=f"Consider handling edge cases like None or invalid inputs in '{fn.name}'.",
                    line=int(getattr(fn, "lineno", 0) or 0),
                    symbol="missing-edge-case-handling",
                )
            )

    return _dedupe_issues(issues)


def generate_fixes(
    syntax_errors: list[AnalyzerIssue],
    runtime_risks: list[AnalyzerIssue],
    logical_issues: list[AnalyzerIssue],
    security_issues: list[AnalyzerIssue],
    semantic_issues: list[AnalyzerIssue],
) -> list[dict[str, Any]]:
    """Generate targeted fixes only for actual syntax/runtime/logical issues."""
    fixes: list[dict[str, Any]] = []
    for issue in [*syntax_errors, *runtime_risks, *logical_issues, *security_issues, *semantic_issues]:
        if issue.symbol == "syntax-error":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Fix Python syntax near the reported line.",
                    "snippet": "# Check brackets, colons, indentation, and string delimiters.",
                }
            )
        elif issue.symbol in {"division-by-zero", "possible-division-by-zero"}:
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Guard denominator before division/modulo.",
                    "snippet": "if denominator == 0:\n    raise ValueError('denominator cannot be zero')",
                }
            )
        elif issue.symbol == "missing-input-validation":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Validate or cast user input before use.",
                    "snippet": "value = input('Enter value: ')\nvalue = int(value)",
                }
            )
        elif issue.symbol == "returns-none-instead-of-raise":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Raise a clear exception for invalid input instead of returning None.",
                    "snippet": "if param is None:\n    raise ValueError('param cannot be None')",
                }
            )
        elif issue.symbol == "missing-return":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Return a value from all computation paths.",
                    "snippet": "return result",
                }
            )
        elif issue.symbol in {"inconsistent-return", "inconsistent-return-path"}:
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Make return behavior consistent across all branches.",
                    "snippet": "if condition:\n    return value\nreturn default_value",
                }
            )
        elif issue.symbol == "sql-injection-risk":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Use parameterized query instead of string interpolation.",
                    "snippet": "cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))",
                }
            )
        elif issue.symbol == "unsafe-dynamic-exec":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Remove eval/exec on untrusted input and use safe parsing/dispatch.",
                    "snippet": "# Replace eval/exec with explicit mapping or parser.",
                }
            )
        elif issue.symbol == "unsafe-subprocess-shell-true":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Use subprocess with argument list and shell=False.",
                    "snippet": "subprocess.run(['cmd', 'arg1'], shell=False, check=True)",
                }
            )
        elif issue.symbol == "hardcoded-secret":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Move secret to environment variable or secret manager.",
                    "snippet": "api_key = os.getenv('API_KEY')",
                }
            )
        elif issue.symbol == "resource-leak-risk":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Close resources explicitly or use context manager.",
                    "snippet": "with sqlite3.connect(db_path) as conn:\n    ...",
                }
            )
        elif issue.symbol == "mutable-default-argument":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Use None as default and initialize inside function.",
                    "snippet": "def fn(x, items=None):\n    if items is None:\n        items = []",
                }
            )
        elif issue.symbol == "builtin-shadowing":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Rename variable to avoid shadowing Python builtins.",
                    "snippet": "items_list = [1, 2, 3]",
                }
            )
        elif issue.symbol == "infinite-recursion-risk":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Add a base condition that stops recursion.",
                    "snippet": "def recurse(n):\n    if n <= 0:\n        return 0\n    return recurse(n - 1)",
                }
            )
        elif issue.symbol == "late-binding-lambda":
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Bind loop variable in lambda default argument.",
                    "snippet": "funcs.append(lambda i=i: i)",
                }
            )
        elif issue.symbol in {"broad-except", "exception-pass-swallow"}:
            fixes.append(
                {
                    "line": issue.line,
                    "issue": issue.message,
                    "fix": "Catch specific exceptions and handle or re-raise them explicitly.",
                    "snippet": "try:\n    ...\nexcept ZeroDivisionError as exc:\n    raise ValueError('Invalid divisor') from exc",
                }
            )

    seen: set[tuple[int, str]] = set()
    unique: list[dict[str, Any]] = []
    for fix in fixes:
        key = (int(fix.get("line", 0) or 0), str(fix.get("fix", "")).strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(fix)
    return unique


def generate_suggestions(best_practice_issues: list[AnalyzerIssue]) -> list[str]:
    """Return relevant, deduplicated suggestions from best-practice findings."""
    seen: set[str] = set()
    suggestions: list[str] = []
    for issue in best_practice_issues:
        text = issue.message.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        suggestions.append(text)
    return suggestions


def calculate_score(
    syntax_errors: list[AnalyzerIssue],
    logical_issues: list[AnalyzerIssue],
    runtime_risks: list[AnalyzerIssue],
    security_issues: list[AnalyzerIssue],
    semantic_issues: list[AnalyzerIssue],
    best_practice_issues: list[AnalyzerIssue],
) -> float:
    """Strict, deterministic score bands with production-ready gate for 10."""
    bugs_count = len(syntax_errors) + len(logical_issues) + len(security_issues) + len(semantic_issues)
    runtime_count = len(runtime_risks)

    symbols = {issue.symbol for issue in best_practice_issues}
    has_validation = "missing-input-type-validation" not in symbols
    has_edge_cases = "missing-edge-case-handling" not in symbols
    has_type_safety = "missing-type-hints" not in symbols

    # Hard rule: 10 only for production-ready code.
    if bugs_count == 0 and runtime_count == 0 and has_validation and has_edge_cases and has_type_safety:
        return 10.0

    # Deterministic raw score.
    raw = 10.0
    raw -= 5.0 * bugs_count
    raw -= 3.0 * runtime_count
    raw -= 1.0 * len(best_practice_issues)
    raw = _clamp(raw, 0.0, 10.0)

    robustness_gaps = int(not has_validation) + int(not has_edge_cases) + int(not has_type_safety)

    # Strict scoring bands.
    if bugs_count > 0:
        return round(_clamp(raw, 0.0, 4.9), 1)
    if runtime_count > 0 or robustness_gaps >= 2:
        return round(_clamp(raw, 5.0, 6.0), 1)
    if robustness_gaps == 1 or len(best_practice_issues) > 0:
        return round(_clamp(raw, 7.0, 8.0), 1)

    # Clean but not fully production-ready should remain high but below 10.
    return round(_clamp(raw, 9.0, 9.9), 1)


def analyze_code(code: str) -> dict[str, Any]:
    """Analyze code and return strict structured output."""
    syntax_errors, tree = detect_syntax_errors(code)
    runtime_risks = detect_runtime_issues(tree)
    runtime_semantic = detect_runtime_semantic_issues(tree)
    exception_smells = detect_exception_handling_smells(tree)
    security_issues = detect_security_issues(tree)
    resource_leaks = detect_resource_leaks(tree)
    runtime_risks = _dedupe_issues([*runtime_risks, *resource_leaks, *runtime_semantic, *exception_smells])
    logical_issues = detect_logical_issues(tree)
    semantic_issues = detect_semantic_issues(tree)
    best_practice_issues = detect_best_practices(tree)

    bugs = [issue.as_dict() for issue in [*syntax_errors, *logical_issues, *security_issues, *semantic_issues]]
    runtime_payload = [issue.as_dict() for issue in runtime_risks]
    fixes = generate_fixes(syntax_errors, runtime_risks, logical_issues, security_issues, semantic_issues)
    suggestions = generate_suggestions(best_practice_issues)
    if any(issue.symbol == "returns-none-instead-of-raise" for issue in runtime_risks):
        explicit = "Raise ValueError for invalid input instead of returning None."
        if explicit not in suggestions:
            suggestions.append(explicit)
    score = calculate_score(
        syntax_errors,
        logical_issues,
        runtime_risks,
        security_issues,
        semantic_issues,
        best_practice_issues,
    )

    return {
        "score": score,
        "bugs": bugs,
        "runtime_risks": runtime_payload,
        "fixes": fixes,
        "suggestions": suggestions,
    }


def run_static_analysis(file_path: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Analyze file content and return backward-compatible payload.

    Existing callers expect:
        { errors, warnings, score }
    """
    _ = timeout_seconds
    path = Path(file_path).expanduser().resolve()

    if not path.exists() or not path.is_file():
        return {
            "errors": [
                {
                    "type": "error",
                    "line": 0,
                    "symbol": "invalid-path",
                    "message": "File does not exist or is not a regular file.",
                    "path": str(path),
                }
            ],
            "warnings": [],
            "score": 0.0,
            "bugs": [],
            "runtime_risks": [],
            "fixes": [],
            "suggestions": [],
        }

    if path.stat().st_size > MAX_FILE_SIZE_BYTES:
        return {
            "errors": [],
            "warnings": [
                {
                    "type": "warning",
                    "line": 0,
                    "symbol": "file-too-large",
                    "message": "Skipped static analysis for file larger than 1 MB.",
                    "path": str(path),
                }
            ],
            "score": 0.0,
            "bugs": [],
            "runtime_risks": [],
            "fixes": [],
            "suggestions": [],
        }

    try:
        code = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.exception("Failed reading file for static analysis: %s", path)
        return {
            "errors": [
                {
                    "type": "error",
                    "line": 0,
                    "symbol": "read-failure",
                    "message": f"Unable to read file: {exc}",
                    "path": str(path),
                }
            ],
            "warnings": [],
            "score": 0.0,
            "bugs": [],
            "runtime_risks": [],
            "fixes": [],
            "suggestions": [],
        }

    analyzed = analyze_code(code)
    return {
        "errors": analyzed["bugs"],
        "warnings": analyzed["runtime_risks"],
        "score": analyzed["score"],
        "bugs": analyzed["bugs"],
        "runtime_risks": analyzed["runtime_risks"],
        "fixes": analyzed["fixes"],
        "suggestions": analyzed["suggestions"],
    }
