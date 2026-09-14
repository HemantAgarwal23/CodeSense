from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


class Settings(BaseSettings):
    app_name: str = Field(default="CodeSense AI API", alias="APP_NAME")
    env: str = Field(default="development", alias="ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    cors_origins: str = Field(default=DEFAULT_CORS_ORIGINS, alias="CORS_ORIGINS")
    max_repo_files: int = Field(default=50, alias="MAX_REPO_FILES")
    max_code_chars: int = Field(default=100_000, alias="MAX_CODE_CHARS")
    llm_model: str = Field(default="openai/gpt-oss-120b", alias="LLM_MODEL")
    llm_fallback_model: str = Field(default="openai/gpt-oss-20b", alias="LLM_FALLBACK_MODEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """Comma-separated CORS_ORIGINS as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
