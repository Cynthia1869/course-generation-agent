from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Course Generation Agent"
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    storage_dir: Path = Field(default=ROOT_DIR / ".data", alias="STORAGE_DIR")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./course_agent.db",
        alias="DATABASE_URL",
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    default_gen_model: str = Field(default="deepseek-chat", alias="DEFAULT_GEN_MODEL")
    default_review_model: str = Field(default="deepseek-chat", alias="DEFAULT_REVIEW_MODEL")
    default_review_threshold: float = Field(default=8.0, alias="DEFAULT_REVIEW_THRESHOLD")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
