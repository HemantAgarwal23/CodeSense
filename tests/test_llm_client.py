import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.llm import langchain_client
from app.main import app

client = TestClient(app)

VALID_REVIEW_JSON = (
    '{"bugs": [], "fixes": [], "suggestions": [], "complexity": "low", '
    '"score": {"readability": 2, "correctness": 3, "efficiency": 2, "best_practices": 2, '
    '"maintainability": 1, "final_score": 10}, "reasoning": "Looks fine."}'
)
EMPTY_RUBRIC = {
    "readability": 0,
    "correctness": 0,
    "efficiency": 0,
    "best_practices": 0,
    "maintainability": 0,
    "final_score": 0,
}


def test_empty_reply_retries_main_prompt_instead_of_repairing() -> None:
    replies = [
        (SimpleNamespace(content=""), 0),
        (SimpleNamespace(content=VALID_REVIEW_JSON), 0),
    ]
    with (
        patch.object(langchain_client.settings, "groq_api_key", "test-key"),
        patch.object(langchain_client, "_invoke_with_model_fallback", new_callable=AsyncMock, side_effect=replies) as invoke,
    ):
        result = asyncio.run(langchain_client.generate_llm_review("x = 1\n", {"errors": [], "warnings": []}))

    assert invoke.await_count == 2
    retry_prompt = str(invoke.await_args_list[1].kwargs["prompt"])
    assert "was not valid JSON" not in retry_prompt
    assert result["score"]["final_score"] == 10


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock)
def test_score_falls_back_to_static_analysis_when_llm_rubric_is_empty(mock_llm: AsyncMock) -> None:
    mock_llm.return_value = {
        "bugs": [],
        "fixes": [],
        "suggestions": [],
        "complexity": "low",
        "score": EMPTY_RUBRIC,
        "reasoning": "",
    }

    response = client.post("/api/v1/review", json={"code": "def add(a: int, b: int) -> int:\n    return a + b\n"})

    assert response.json()["summary"]["final_score"] > 0


def test_only_fallback_model_uses_low_reasoning_effort() -> None:
    with patch.object(langchain_client.settings, "groq_api_key", "test-key"):
        fallback = langchain_client._build_llm("openai/gpt-oss-20b")
        primary = langchain_client._build_llm("openai/gpt-oss-120b")

    assert fallback.model_kwargs == {"reasoning_effort": "low"}
    assert primary.model_kwargs == {}
