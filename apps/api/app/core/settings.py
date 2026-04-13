from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env.local", ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Course Generation Agent"
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    cors_allow_origin_regex: str = Field(
        default=r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?$",
        alias="CORS_ALLOW_ORIGIN_REGEX",
    )
    storage_dir: Path = Field(default=ROOT_DIR / ".data", alias="STORAGE_DIR")
    deepseek_config_file: Path = Field(default=ROOT_DIR / "config" / "deepseek.yaml", alias="DEEPSEEK_CONFIG_FILE")
    prompt_root_dir: Path = Field(default=ROOT_DIR / "prompts", alias="PROMPT_ROOT_DIR")
    database_url: str = Field(default="sqlite+aiosqlite:///./course_agent.db", alias="DATABASE_URL")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    default_review_threshold: float = Field(default=8.0, alias="DEFAULT_REVIEW_THRESHOLD")
    max_auto_optimization_loops: int = Field(default=2, alias="MAX_AUTO_OPTIMIZATION_LOOPS")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.deepseek_config_file.parent.mkdir(parents=True, exist_ok=True)
    settings.prompt_root_dir.mkdir(parents=True, exist_ok=True)
    return settings
