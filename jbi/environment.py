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

    # Identity of JBI's own service accounts, used to suppress the events its
    # own writes generate (Invariant C of the bidirectional sync plan). Unset
    # means "no suppression", which is today's behavior.
    jira_bot_account_id: Optional[str] = None
    bugzilla_bot_login: Optional[str] = None

    # Pub/Sub pull consumer (`python -m jbi consume`). Unset project/
    # subscription means the consumer cannot start; the web service is
    # unaffected either way.
    pubsub_project_id: Optional[str] = None
    pubsub_subscription_id: Optional[str] = None
    # Never 1: a single slot serialises every ordering key, so a backlog
    # cannot drain before the pull window closes and held ordered messages
    # are stranded. Per-key ordering is the subscription's job, not this
    # setting's.
    pubsub_max_concurrent_messages: int = 10
    pubsub_max_lease_duration: int = 600
    # Must stay below both the lease duration and any Cloud Run job task
    # timeout, so the process exits cleanly rather than being killed.
    pubsub_pull_timeout_seconds: int = 540
    pubsub_shutdown_grace_seconds: int = 3

    # Phabricator
    phabricator_base_url: str = "https://phabricator.services.mozilla.com"

    # Logging
    log_level: str = "info"
    log_format: str = "json"  # set to "text" for human-readable logs

    # Sentry
    sentry_dsn: Optional[AnyUrl] = None
    sentry_traces_sample_rate: float = 1.0

    # Retry queue
    dl_queue_dsn: FileUrl

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the Settings object; use cache"""
    return Settings()
