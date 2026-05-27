from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="AI Code Review Copilot API", alias="APP_NAME")
    env: str = Field(default="development", alias="ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    max_repo_files: int = Field(default=2000, alias="MAX_REPO_FILES")
    llm_model: str = Field(default="llama3-70b-8192", alias="LLM_MODEL")
    llm_fallback_model: str = Field(default="llama3-70b-8192", alias="LLM_FALLBACK_MODEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
