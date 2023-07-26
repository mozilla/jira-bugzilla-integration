"""
Parsing and validating the YAML configuration occurs within this module
"""
import logging
from functools import lru_cache

from pydantic import ValidationError
from pydantic_yaml import parse_yaml_raw_as

from jbi import environment
from jbi.models import Actions

settings = environment.get_settings()
logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Error when an exception occurs during processing config"""


@lru_cache
def get_actions() -> Actions:
    """Load actions from file determined by ENV name"""
    return get_actions_from_file(f"config/config.{settings.env}.yaml")


def get_actions_from_file(jbi_config_file: str) -> Actions:
    """Convert and validate YAML configuration to `Action` objects"""
    try:
        with open(jbi_config_file, encoding="utf8") as file:
            content = file.read()
            actions: Actions = parse_yaml_raw_as(Actions, content)
        return actions
    except ValidationError as exception:
        logger.exception(exception)
        raise ConfigError("Errors exist.") from exception
