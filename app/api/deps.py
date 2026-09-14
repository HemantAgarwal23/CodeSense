from app.core.config import get_settings
from app.services.review_service import ReviewService


def get_review_service() -> ReviewService:
    return ReviewService(max_concurrency=get_settings().llm_max_concurrency)
