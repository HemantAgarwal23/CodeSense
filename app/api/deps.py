from app.services.review_service import ReviewService


def get_review_service() -> ReviewService:
    return ReviewService()

