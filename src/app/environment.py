import json
import os
from functools import lru_cache

from pydantic import BaseSettings


class Settings(BaseSettings):
    jbi_action_key: str = "jbi"
    jbi_folder_path: str = "src/jbi/whiteboard_tags/"


@lru_cache()
def get_settings() -> Settings:
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
