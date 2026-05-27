from typing import List, Optional

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


class Issue(BaseModel):
    file: str
    line: int
    severity: str
    message: str


class ReviewResponse(BaseModel):
    summary: str
    issues: List[Issue] = Field(default_factory=list)
    llm_recommendations: List[str] = Field(default_factory=list)
