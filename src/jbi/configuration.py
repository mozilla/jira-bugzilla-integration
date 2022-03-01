import importlib
from inspect import signature
from types import ModuleType
from typing import Any, Dict, Optional

import yaml
from yaml import Loader

from src.app import environment

settings = environment.get_settings()


class ConfigError(Exception):
    pass


class ProcessError(Exception):
    pass


def get_yaml_configurations(
    jbi_config_file: str = "config.yaml", process_only_enabled: bool = True
) -> Dict[str, Dict]:

    with open(jbi_config_file, encoding="utf-8") as file:
        try:
            file_data = file.read()
            data = yaml.load(file_data, Loader)
            validated_action_dict = process_actions(
                action_configuration=data.get("actions"),
                process_only_enabled=process_only_enabled,
            )
            return validated_action_dict
        except (ValueError, ConfigError, yaml.YAMLError) as exception:
            raise ProcessError("PROCESS ERROR: Errors exist.") from exception


def process_actions(
    action_configuration, process_only_enabled: bool = True
) -> Dict[str, Dict]:
    requested_actions = {}
    for yaml_action_key, inner_action_dict in action_configuration.items():
        # Check enabled state
        enabled = inner_action_dict.get("enabled")
        if enabled or not process_only_enabled:
            validate_action_yaml_jbi_naming(
                yaml_action_key=yaml_action_key, action_dict=inner_action_dict
            )
            validate_action_yaml_module(action_dict=inner_action_dict)
            requested_actions[yaml_action_key] = inner_action_dict
    return requested_actions


def validate_action_yaml_jbi_naming(yaml_action_key, action_dict):
    # Validate yaml_action_key == parameters.whiteboard_tag
    try:
        action_parameters = action_dict.get("parameters")
        wb_tag = action_parameters.get("whiteboard_tag")
        if yaml_action_key != wb_tag:
            raise ConfigError(
                f"CONFIG ERROR: `{yaml_action_key}` setup, but expected '{wb_tag}'"
                "(from the `parameters.whiteboard_tag` field)."
            )
    except (TypeError, AttributeError) as exception:
        raise ConfigError("CONFIG ERROR: Action is not properly setup.") from exception


def validate_action_yaml_module(action_dict: Dict[str, Any]):
    # Validate action: exists, has init function, and has expected params
    try:
        action: str = action_dict.get("action")  # type: ignore
        action_parameters: Optional[Dict[str, Any]] = action_dict.get("parameters")
        action_module: ModuleType = importlib.import_module(action)
        if not action_module:
            raise TypeError("Module is not found.")
        if not hasattr(action_module, "init"):
            raise TypeError("Module is missing `init` method.")

        signature(action_module.init).bind(**action_parameters)  # type: ignore
    except ImportError as exception:
        raise ConfigError(f"CONFIG ERROR: Unknown action `{action}`.") from exception
    except (TypeError, AttributeError) as exception:
        raise ConfigError("CONFIG ERROR: Action is not properly setup.") from exception
