from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import get_review_service
from app.models.review import ReviewRequest
from app.services.review_service import ReviewService

router = APIRouter()
logger = logging.getLogger(__name__)


class ReviewApiRequest(BaseModel):
    """Public API request contract for review endpoint."""

    code: str | None = None
    repo_url: str | None = None
    provider: Literal["groq"] = "groq"
    branch: str = "main"
    max_files: int | None = Field(default=None, ge=1)


class ReviewApiResponse(BaseModel):
    """Public API response contract."""

    files: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    final_score: float = 0.0
    processing_time: int = 0


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


@router.post("", response_model=ReviewApiResponse)
async def review_code(
    payload: ReviewApiRequest,
    response: Response,
    service: ReviewService = Depends(get_review_service),
) -> ReviewApiResponse:
    """Run code review for repository or raw code payload."""
    start = perf_counter()
    logger.info(
        "Review request started. provider=%s has_code=%s has_repo_url=%s max_files=%s",
        payload.provider,
        bool(payload.code),
        bool(payload.repo_url),
        payload.max_files,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    if not payload.code and not payload.repo_url:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_input",
            "At least one of 'code' or 'repo_url' must be provided.",
        )

    try:
        request_model = ReviewRequest(
            repo_url=payload.repo_url,
            code_snippet=payload.code,
            branch=payload.branch,
            max_files=payload.max_files,
        )
    except (ValueError, ValidationError) as exc:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_input",
            str(exc),
        ) from exc

    try:
        result = await service.run_review(request_model)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Review pipeline execution failed.")
        raise _http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "review_processing_failed",
            "Failed to process review request.",
        )

    summary = result.get("summary", {}) if isinstance(result, dict) else {}
    final_score = float(summary.get("final_score", 0.0) or 0.0)
    elapsed_ms = int((perf_counter() - start) * 1000)

    logger.info(
        "Review request completed. provider=%s elapsed_ms=%d final_score=%s",
        payload.provider,
        elapsed_ms,
        final_score,
    )

    return ReviewApiResponse(
        files=result.get("files", []) if isinstance(result, dict) else [],
        summary=summary,
        final_score=final_score,
        processing_time=elapsed_ms,
    )
