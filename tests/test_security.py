from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_review_rejects_oversized_code() -> None:
    with patch("app.api.v1.routes.review.get_settings", return_value=MagicMock(max_code_chars=10)):
        response = client.post("/api/v1/review", json={"code": "x = 1234567890"})

    assert response.status_code == 413
    assert response.json()["detail"]["error"]["code"] == "code_too_large"


def test_review_rejects_branch_that_looks_like_a_git_option() -> None:
    response = client.post(
        "/api/v1/review",
        json={"repo_url": "https://github.com/octocat/Hello-World", "branch": "--upload-pack=touch /tmp/pwned"},
    )

    assert response.status_code == 422


def test_cors_allows_only_configured_origins() -> None:
    preflight = {"Access-Control-Request-Method": "POST"}
    allowed = client.options("/api/v1/review", headers={"Origin": "http://localhost:5173", **preflight})
    blocked = client.options("/api/v1/review", headers={"Origin": "https://evil.example", **preflight})

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers
