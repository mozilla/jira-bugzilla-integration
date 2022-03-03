"""
Module dedicated to interacting with the environment (variables, version.json)
"""
import json
import os
from functools import lru_cache

from pydantic import BaseSettings


class Settings(BaseSettings):
    """
    The Settings object extracts environment variables for convenience
    """

    host: str = "0.0.0.0"
    port: str = "80"
    app_reload: bool = True
    env: str = "dev"

    # Jira
    jira_base_url: str = "https://jira.allizom.org/"
    jira_username: str
    jira_password: str

    # Bugzilla
    bugzilla_base_url: str = "https://bugzilla-dev.allizom.org/"
    bugzilla_api_key: str

    # Logging
    log_level: str = "info"


@lru_cache()
def get_settings() -> Settings:
    """
    Return the Settings object; use cache
    """
    return Settings()


@lru_cache()
def get_version():
    """
    Return contents of version.json.
    This has generic data in repo, but gets the build details in CI.
    """
    _root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    version_path = os.path.join(_root, "version.json")
    info = {}
    if os.path.exists(version_path):
        with open(version_path, "r", encoding="utf8") as version_file:
            info = json.load(version_file)
    return info
