from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.review_service import ReviewService

client = TestClient(app)

LLM_RESULT = {
    "bugs": [],
    "fixes": [],
    "suggestions": [],
    "complexity": "low",
    "score": {
        "readability": 2,
        "correctness": 3,
        "efficiency": 2,
        "best_practices": 2,
        "maintainability": 1,
        "final_score": 10,
    },
    "reasoning": "",
}


@pytest.mark.parametrize(
    ("code", "filename", "expected"),
    [
        ("def add(a, b):\n    return a + b\n", None, "snippet.py"),
        ("def broken(\n    return 1\n", None, "snippet.py"),
        ("function add(a, b) {\n  return a + b;\n}\n", None, "snippet.js"),
        ("interface User {\n  name: string;\n}\n", None, "snippet.ts"),
        ('#include <iostream>\nint main() { std::cout << "hi"; }\n', None, "snippet.cpp"),
        ("public class Main {\n  public static void main(String[] a) {}\n}\n", None, "snippet.java"),
        ("anything at all", "service.ts", "service.ts"),
        ("anything at all", "notes.txt", "snippet.py"),
    ],
)
def test_snippet_filename_matches_language(code: str, filename: str | None, expected: str) -> None:
    assert ReviewService._snippet_filename(code, filename) == expected


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock, return_value=LLM_RESULT)
def test_javascript_snippet_gets_no_python_syntax_error(_mock_llm: AsyncMock) -> None:
    code = "function add(a, b) {\n  return a + b;\n}\nconsole.log(add(1, 2));\n"

    data = client.post("/api/v1/review", json={"code": code}).json()

    assert data["files"][0]["file"] == "snippet.js"
    descriptions = [item["description"] for items in data["findings"].values() for item in items]
    assert not any("invalid syntax" in description for description in descriptions)


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock, return_value=LLM_RESULT)
def test_broken_python_snippet_still_reports_syntax_error(_mock_llm: AsyncMock) -> None:
    data = client.post("/api/v1/review", json={"code": "def broken(\n    return 1\n"}).json()

    categories = [item["category"] for item in data["findings"]["code_issues"]]
    assert "syntax" in categories


def test_review_rejects_filename_with_path() -> None:
    response = client.post("/api/v1/review", json={"code": "x = 1", "filename": "../../etc/passwd"})

    assert response.status_code == 422
