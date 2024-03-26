"""
Module dedicated to interacting with the environment (variables, version.json)
"""

# https://github.com/python/mypy/issues/12841
from enum import StrEnum, auto  # type: ignore
from functools import lru_cache
from typing import Optional

from pydantic import AnyUrl, FileUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Production environment choices"""

    LOCAL = auto()
    NONPROD = auto()
    PROD = auto()


class Settings(BaseSettings):
    """The Settings object extracts environment variables for convenience."""

    host: str = "0.0.0.0"
    port: int = 8000
    app_reload: bool = False
    app_debug: bool = False
    max_retries: int = 3
    # https://github.com/python/mypy/issues/12841
    env: Environment = Environment.NONPROD  # type: ignore
    jbi_api_key: str

    # Jira
    jira_base_url: str = "https://mozit-test.atlassian.net/"
    jira_username: str
    jira_api_key: str

    # Bugzilla
    bugzilla_base_url: str = "https://bugzilla-dev.allizom.org"
    bugzilla_api_key: str

    # Logging
    log_level: str = "info"
    log_format: str = "json"  # set to "text" for human-readable logs

    # Sentry
    sentry_dsn: Optional[AnyUrl] = None
    sentry_traces_sample_rate: float = 1.0

    # Retry queue
    constant_retry: str = "false"
    retry_timeout: int = 7
    dl_queue_dsn: FileUrl

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the Settings object; use cache"""
    return Settings()
