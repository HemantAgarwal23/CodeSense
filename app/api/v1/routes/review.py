from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import get_review_service
from app.core.config import get_settings
from app.models.review import ReviewRequest
from app.services.review_service import ReviewService
from app.utils.github_pr import InvalidPullRequestUrlError, PullRequestAccessError, PullRequestError

router = APIRouter()
logger = logging.getLogger(__name__)

# Git ref names only; a leading "-" could be read as a git command-line option.
BRANCH_PATTERN = r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$"
# Plain file names only (no paths); the extension tells the service which language a snippet is.
FILENAME_PATTERN = r"^[A-Za-z0-9._ -]+$"


class ReviewApiRequest(BaseModel):
    """Public API request contract for review endpoint."""

    code: str | None = None
    filename: str | None = Field(default=None, max_length=255, pattern=FILENAME_PATTERN)
    repo_url: str | None = None
    provider: Literal["groq"] = "groq"
    branch: str = Field(default="main", max_length=255, pattern=BRANCH_PATTERN)
    max_files: int | None = Field(default=None, ge=1)


class PullRequestReviewApiRequest(BaseModel):
    """Request contract for reviewing a GitHub pull request."""

    pr_url: str
    max_files: int | None = Field(default=None, ge=1)
    post_comments: bool = False


class ReviewApiResponse(BaseModel):
    """Public API response contract."""

    files: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    final_score: float = 0.0
    processing_time: int = 0
    findings: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class PullRequestReviewApiResponse(ReviewApiResponse):
    """Review response plus pull request details and inline comments."""

    pull_request: dict[str, Any] = Field(default_factory=dict)
    review_comments: list[dict[str, Any]] = Field(default_factory=list)


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


def _disable_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _response_fields(result: dict[str, Any], start: float) -> dict[str, Any]:
    summary = result.get("summary", {})
    return {
        "files": result.get("files", []),
        "summary": summary,
        "final_score": float(summary.get("final_score", 0.0) or 0.0),
        "processing_time": int((perf_counter() - start) * 1000),
        "findings": result.get("findings", {}),
    }


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
    _disable_caching(response)

    if not payload.code and not payload.repo_url:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_input",
            "At least one of 'code' or 'repo_url' must be provided.",
        )

    max_code_chars = get_settings().max_code_chars
    if payload.code and len(payload.code) > max_code_chars:
        raise _http_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "code_too_large",
            f"Code exceeds the {max_code_chars:,}-character limit.",
        )

    try:
        request_model = ReviewRequest(
            repo_url=payload.repo_url,
            code_snippet=payload.code,
            filename=payload.filename,
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

    fields = _response_fields(result, start)
    logger.info(
        "Review request completed. provider=%s elapsed_ms=%d final_score=%s",
        payload.provider,
        fields["processing_time"],
        fields["final_score"],
    )
    return ReviewApiResponse(**fields)


@router.post("/pr", response_model=PullRequestReviewApiResponse)
async def review_pull_request(
    payload: PullRequestReviewApiRequest,
    response: Response,
    service: ReviewService = Depends(get_review_service),
) -> PullRequestReviewApiResponse:
    """Review the supported files changed in a GitHub pull request."""
    start = perf_counter()
    logger.info(
        "Pull request review started. max_files=%s post_comments=%s",
        payload.max_files,
        payload.post_comments,
    )
    _disable_caching(response)

    if payload.post_comments and not get_settings().github_token:
        raise _http_error(
            status.HTTP_400_BAD_REQUEST,
            "github_token_required",
            "Set GITHUB_TOKEN on the server to post review comments to GitHub.",
        )

    try:
        result = await service.run_pr_review(payload.pr_url, payload.max_files, payload.post_comments)
    except InvalidPullRequestUrlError as exc:
        raise _http_error(status.HTTP_400_BAD_REQUEST, "invalid_input", str(exc)) from exc
    except PullRequestAccessError as exc:
        raise _http_error(status.HTTP_403_FORBIDDEN, "github_access_denied", str(exc)) from exc
    except PullRequestError as exc:
        raise _http_error(status.HTTP_502_BAD_GATEWAY, "github_request_failed", str(exc)) from exc
    except Exception:
        logger.exception("Pull request review failed.")
        raise _http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "review_processing_failed",
            "Failed to process pull request review.",
        )

    fields = _response_fields(result, start)
    logger.info(
        "Pull request review completed. elapsed_ms=%d comments=%d",
        fields["processing_time"],
        len(result.get("review_comments", [])),
    )
    return PullRequestReviewApiResponse(
        **fields,
        pull_request=result.get("pull_request", {}),
        review_comments=result.get("review_comments", []),
    )
