"""Application configuration, loaded from the environment and ``.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime settings for every layer of the platform.

    Every field can be overridden with a ``DATAFORGE_``-prefixed environment
    variable, e.g. ``DATAFORGE_LOG_LEVEL=DEBUG``.
    """

    model_config = SettingsConfigDict(
        env_prefix="DATAFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = False

    # Filesystem layout
    data_dir: Path = PROJECT_ROOT / "data"
    artifact_dir: Path = PROJECT_ROOT / "artifacts"
    model_dir: Path = PROJECT_ROOT / "models"

    # Deduplication defaults
    match_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    blocking_key_length: int = Field(default=4, ge=1)

    # Machine learning
    model_name: str = "matcher"
    random_seed: int = 42

    # Scraping
    scrape_user_agent: str = "DataForge/0.1 (+https://github.com/Mohammed-HeshamMohammed/DataForge)"
    scrape_timeout_seconds: float = 20.0
    scrape_delay_seconds: float = 1.0
    scrape_max_retries: int = 3
    scrape_respect_robots: bool = True

    # Browser automation
    selenium_browser: str = "chrome"
    selenium_headless: bool = True
    selenium_timeout_seconds: float = 30.0
    selenium_remote_url: str | None = None

    # Web
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    web_reload: bool = False

    def ensure_directories(self) -> None:
        """Create the directories the platform writes into."""
        for directory in (self.data_dir, self.artifact_dir, self.model_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
