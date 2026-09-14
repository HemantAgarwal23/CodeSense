import ast
import asyncio

from benchmarks.cases import BUG_MARKER, CASES
from benchmarks.run_benchmark import evaluate_mode, prepare_case, summarize


def test_benchmark_cases_are_valid_and_labeled() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))

    for case in CASES:
        code, bug_line = prepare_case(case)
        ast.parse(code)
        assert BUG_MARKER not in code
        assert case["code"].count(BUG_MARKER) == (1 if case["keywords"] else 0)
        assert (bug_line is not None) == bool(case["keywords"])


def test_static_benchmark_runs_offline() -> None:
    summary = summarize(asyncio.run(evaluate_mode("static", CASES)))

    assert summary["cases_failed"] == 0
    assert summary["buggy_cases"] + summary["clean_cases"] == len(CASES)
