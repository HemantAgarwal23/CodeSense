from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.utils.github_pr import (
    ChangedFile,
    InvalidPullRequestUrlError,
    PullRequest,
    PullRequestRef,
    changed_lines_from_patch,
    parse_pull_request_url,
)

client = TestClient(app)

MOCK_LLM_RESULT = {
    "bugs": [],
    "fixes": [],
    "suggestions": [],
    "complexity": "low",
    "score": {
        "readability": 2,
        "correctness": 1,
        "efficiency": 2,
        "best_practices": 1,
        "maintainability": 1,
        "final_score": 7,
    },
    "reasoning": "Mocked LLM response for tests.",
}

PULL_REQUEST = PullRequest(
    ref=PullRequestRef("octocat", "Hello-World", 7),
    title="Add stats helpers",
    html_url="https://github.com/octocat/Hello-World/pull/7",
    head_sha="abc123",
    files=[
        ChangedFile(
            path="src/stats.py",
            content="def average(values):\n    return 10 / 0\n",
            changed_lines={2},
        )
    ],
    supported_files_changed=1,
)


def test_parse_pull_request_url() -> None:
    ref = parse_pull_request_url("https://github.com/octocat/Hello-World/pull/42/files")
    assert (ref.owner, ref.repo, ref.number) == ("octocat", "Hello-World", 42)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/octocat/Hello-World",
        "https://gitlab.com/octocat/Hello-World/pull/1",
        "http://github.com/octocat/Hello-World/pull/1",
    ],
)
def test_parse_pull_request_url_rejects_other_urls(url: str) -> None:
    with pytest.raises(InvalidPullRequestUrlError):
        parse_pull_request_url(url)


def test_changed_lines_from_patch_tracks_new_file_line_numbers() -> None:
    patch_text = "\n".join(
        [
            "@@ -1,4 +1,5 @@",
            " def average(values):",
            "-    return sum(values) / len(values)",
            "+    if not values:",
            "+        return 0.0",
            "+    return sum(values) / len(values)",
            " ",
            "@@ -20,2 +21,3 @@ def other():",
            " x = 1",
            "+y = 2",
            "\\ No newline at end of file",
        ]
    )
    assert changed_lines_from_patch(patch_text) == {2, 3, 4, 22}


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock, return_value=MOCK_LLM_RESULT)
@patch("app.services.review_service.fetch_pull_request", new_callable=AsyncMock, return_value=PULL_REQUEST)
def test_pull_request_review_comments_on_changed_lines(mock_fetch: AsyncMock, _mock_llm: AsyncMock) -> None:
    response = client.post("/api/v1/review/pr", json={"pr_url": PULL_REQUEST.html_url, "max_files": 5})

    assert response.status_code == 200
    data = response.json()
    assert data["pull_request"]["title"] == "Add stats helpers"
    assert data["pull_request"]["files_reviewed"] == 1
    assert data["files"][0]["file"] == "src/stats.py"
    assert any(comment["path"] == "src/stats.py" and comment["line"] == 2 for comment in data["review_comments"])
    assert data["findings"]["runtime_risks"]
    assert mock_fetch.await_args.kwargs["max_files"] == 5


def test_pull_request_review_requires_token_to_post_comments() -> None:
    with patch("app.api.v1.routes.review.get_settings", return_value=MagicMock(github_token="")):
        response = client.post(
            "/api/v1/review/pr",
            json={"pr_url": PULL_REQUEST.html_url, "post_comments": True},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "github_token_required"


def test_pull_request_review_rejects_invalid_url() -> None:
    response = client.post("/api/v1/review/pr", json={"pr_url": "https://github.com/octocat/Hello-World"})

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "invalid_input"
