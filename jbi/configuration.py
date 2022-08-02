"""
Parsing and validating the YAML configuration occurs within this module
"""
import logging
from functools import lru_cache

from pydantic import ValidationError

from jbi import environment
from jbi.models import Actions

settings = environment.get_settings()
logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Error when an exception occurs during processing config"""


@lru_cache
def get_actions(
    jbi_config_file: str = f"config/config.{settings.env}.yaml",
) -> Actions:
    """Convert and validate YAML configuration to `Action` objects"""
    try:
        actions: Actions = Actions.parse_file(jbi_config_file)
        return actions
    except ValidationError as exception:
        logger.exception(exception)
        raise ConfigError("Errors exist.") from exception
