"""Central configuration, read from the project-root .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
CONFIG_DIR = BACKEND_DIR / "config"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    database_url_override: str
    fred_api_key: str
    refresh_interval_minutes: int
    sentiment_engine: str

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def server_url(self) -> str:
        """MySQL URL without a schema, used once to CREATE DATABASE IF NOT EXISTS."""
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/?charset=utf8mb4"
        )


def get_settings() -> Settings:
    return Settings(
        db_host=os.getenv("DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("DB_PORT", "3306") or 3306),
        db_user=os.getenv("DB_USER", ""),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_name=os.getenv("DB_NAME", "fx_dashboard"),
        database_url_override=os.getenv("DATABASE_URL", ""),
        fred_api_key=os.getenv("FRED_API_KEY", ""),
        refresh_interval_minutes=int(os.getenv("REFRESH_INTERVAL_MINUTES", "0") or 0),
        sentiment_engine=os.getenv("SENTIMENT_ENGINE", "lexicon").lower(),
    )


settings = get_settings()
