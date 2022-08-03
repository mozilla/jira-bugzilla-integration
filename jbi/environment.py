"""
Module dedicated to interacting with the environment (variables, version.json)
"""
import json
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import AnyUrl, BaseSettings


class Environment(str, Enum):
    """Production environment choices"""

    LOCAL = "local"
    NONPROD = "nonprod"
    PROD = "prod"


class SentryDsn(AnyUrl):
    """Url type to validate Sentry DSN"""


class Settings(BaseSettings):
    """The Settings object extracts environment variables for convenience."""

    host: str = "0.0.0.0"
    port: int = 8000
    app_reload: bool = False
    app_debug: bool = False
    max_retries: int = 3
    env: Environment = Environment.NONPROD

    # Jira
    jira_base_url: str = "https://mozit-test.atlassian.net/"
    jira_username: str
    jira_api_key: str

    # Bugzilla
    bugzilla_base_url: str = "https://bugzilla-dev.allizom.org"
    bugzilla_api_key: str

    # Logging
    log_level: str = "debug"

    # Sentry
    sentry_dsn: Optional[SentryDsn]

    class Config:
        """Pydantic configuration"""

        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Return the Settings object; use cache"""
    return Settings()


@lru_cache()
def get_version():
    """Return contents of version.json. This has generic data in repo, but gets the build details in CI."""
    info = {}
    version_path = Path(__file__).parents[1] / "version.json"
    if version_path.exists():
        info = json.loads(version_path.read_text(encoding="utf8"))
    return info
