from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from langchain.prompts import ChatPromptTemplate

from app.llm.langchain_client import _build_fix_only_prompt, _build_prompt, _build_repair_prompt
from app.main import app

client = TestClient(app)

CART_CODE = "class Cart:\n    items = []\n\n    def add(self, item):\n        self.items.append(item)\n"
SHARED_STATE_BUG = "The items list is a class attribute, so every Cart shares the same list."


def _llm_result(bugs: list, status: str | None = None) -> dict:
    result = {
        "bugs": bugs,
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
        "reasoning": "",
    }
    if status:
        result["status"] = status
    return result


def _bug_descriptions(data: dict) -> list[str]:
    findings = data["findings"]
    return [item["description"] for key in ("runtime_risks", "code_issues") for item in findings[key]]


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock)
def test_llm_bugs_are_kept_regardless_of_wording(mock_llm: AsyncMock) -> None:
    mock_llm.return_value = _llm_result(
        [
            SHARED_STATE_BUG,
            {"description": "Carts with millions of items may be slow to copy.", "line": 5, "confidence": "low"},
        ]
    )

    descriptions = _bug_descriptions(client.post("/api/v1/review", json={"code": CART_CODE}).json())

    assert SHARED_STATE_BUG in descriptions
    assert not any("millions of items" in description for description in descriptions)


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock)
def test_llm_failure_placeholder_is_not_reported_as_a_bug(mock_llm: AsyncMock) -> None:
    mock_llm.return_value = _llm_result(["LLM failed to generate valid response"], status="failed")

    descriptions = _bug_descriptions(client.post("/api/v1/review", json={"code": CART_CODE}).json())

    assert not any(description.startswith("LLM failed") for description in descriptions)


def test_prompts_containing_braces_format_without_template_errors() -> None:
    code = "config = {'retries': 3}\n"
    prompts = [
        _build_prompt(code, {"errors": [], "warnings": []}),
        _build_repair_prompt('{"bugs": ['),
        _build_fix_only_prompt(code, [{"description": "bug"}]),
    ]
    for messages in prompts:
        ChatPromptTemplate.from_messages(messages).format_messages()


def test_prompt_numbers_code_lines() -> None:
    human_prompt = _build_prompt("a = 1\nb = 2\n", {"errors": [], "warnings": []})[1][1]

    assert "1 | a = 1" in human_prompt
    assert "2 | b = 2" in human_prompt


@patch("app.services.review_service.generate_llm_review", new_callable=AsyncMock)
def test_bare_code_fix_from_llm_is_offered_as_a_fix(mock_llm: AsyncMock) -> None:
    fixed_code = "class Cart:\n    def __init__(self):\n        self.items = []\n"
    result = _llm_result([SHARED_STATE_BUG])
    result["fixes"] = [fixed_code]
    mock_llm.return_value = result

    fixes = client.post("/api/v1/review", json={"code": CART_CODE}).json()["findings"]["fixes"]

    assert any(fix["code"] == fixed_code.strip() for fix in fixes)
