"""
Parsing and validating the YAML configuration occurs within this module
"""
import logging
from functools import lru_cache

from pydantic import ValidationError

from src.app import environment
from src.jbi.model import Actions

settings = environment.get_settings()
jbi_logger = logging.getLogger("src.jbi")


class ConfigError(Exception):
    """Error when an exception occurs during processing config"""


@lru_cache
def get_actions(
    jbi_config_file: str = f"config/config.{settings.env}.yaml",
) -> Actions:
    """Convert and validate YAML configuration to Action object"""

    with open(jbi_config_file, encoding="utf-8") as file:
        try:
            yaml_data = file.read()
            actions: Actions = Actions.parse_raw(yaml_data)
            return actions
        except ValidationError as exception:
            jbi_logger.exception(exception)
            raise ConfigError("Errors exist.") from exception


def get_actions_dict():
    """Returns dict of"""
    return get_actions().dict()["actions"]
