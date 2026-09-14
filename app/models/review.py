from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ReviewRequest(BaseModel):
    repo_url: Optional[str] = None
    code_snippet: Optional[str] = None
    branch: str = "main"
    max_files: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_payload(self) -> "ReviewRequest":
        if not self.repo_url and not self.code_snippet:
            raise ValueError("repo_url or code_snippet must be provided")
        return self

