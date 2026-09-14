from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.utils.static_analysis import analyze_code

client = TestClient(app)

MOCK_LLM_RESULT = {
    "bugs": [],
    "fixes": [],
    "suggestions": ["Consider adding input validation."],
    "complexity": "low",
    "score": {
        "readability": 2,
        "correctness": 2,
        "efficiency": 2,
        "best_practices": 1,
        "maintainability": 1,
        "final_score": 8,
    },
    "reasoning": "Mocked LLM response for tests.",
}


def test_review_requires_input() -> None:
    response = client.post("/api/v1/review", json={})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "invalid_input"


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock)
def test_review_code_snippet(mock_llm: AsyncMock) -> None:
    mock_llm.return_value = MOCK_LLM_RESULT

    response = client.post(
        "/api/v1/review",
        json={"code": "def add(a, b):\n    return a + b\n"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["files"]) == 1
    assert data["summary"]["status"] == "ok"
    assert "processing_time" in data
    mock_llm.assert_awaited()


def test_static_analysis_detects_division_risk() -> None:
    result = analyze_code("def divide(a, b):\n    return 10 / 0\n")
    symbols = {issue.get("symbol") for issue in result["runtime_risks"]}
    assert "division-by-zero" in symbols

def test_static_analysis_detects_syntax_error() -> None:
    result = analyze_code("def broken(\n    return 1\n")
    assert result["bugs"]


@patch("app.services.review_service.get_settings")
@patch("app.services.review_service.process_github_repository")
def test_repo_review_caps_file_count(mock_process_repo, mock_settings) -> None:
    mock_settings.return_value.max_repo_files = 3
    mock_process_repo.return_value = [f"/tmp/repo/node_modules/dep{i}.js" for i in range(5)] + [
        f"/tmp/repo/module{i}.py" for i in range(10)
    ]

    with patch(
        "app.services.review_service.ReviewService._process_files",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_process_files:
        client.post(
            "/api/v1/review",
            json={"repo_url": "https://github.com/octocat/Hello-World", "max_files": 100},
        )

    reviewed = mock_process_files.await_args.args[0]
    assert reviewed == [f"/tmp/repo/module{i}.py" for i in range(3)]
