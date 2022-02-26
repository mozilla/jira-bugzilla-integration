import json
from inspect import signature
from pathlib import Path
from types import ModuleType
from typing import Dict, Optional

from src.app import environment
from src.jbi import actions

settings = environment.get_settings()


class ConfigError(Exception):
    pass


class ProcessError(Exception):
    pass


def process_config_per_file(filename: str):
    """
        Process and validate (or error) for a single JBI config file
    :param filename:
    :return: dict of whiteboard_tag:json_file_values
    """
    default_dict = {
        "action": "src.jbi.whiteboard_actions.default",
        "enabled": True,
    }  # Could be migrated to a json file
    with open(filename, encoding="utf-8") as file:
        data = json.load(file)
        file_dict = default_dict.copy()
        file_dict.update(data)

        # Only process enabled files
        if not file_dict.get("enabled"):
            return None, None

        # Confirm action exists
        action = file_dict.get("action")
        if action not in actions.module_dict.keys():
            raise ConfigError(f"CONFIG ERROR: Unknown action `{action}`.")

        # Validate action: exists, has init function, and has expected params
        try:
            action_module: Optional[ModuleType] = actions.module_dict.get(str(action))
            if not action_module:
                raise TypeError("Module is not found.")
            if not hasattr(action_module, "init"):
                raise TypeError("Module is missing `init` method.")

            signature(action_module.init).bind(**file_dict)  # type: ignore
        except TypeError as exception:
            raise ConfigError(
                "CONFIG ERROR: Action is not properly setup."
            ) from exception

        # Validate files: naming
        wb_key = file_dict.get("whiteboard_tag")
        json_file = filename.split("/")[-1]
        extracted_filename = json_file.replace(".json", "")
        if not wb_key == extracted_filename:
            raise ConfigError(
                f"CONFIG ERROR: `{json_file}` found, but expected string '{wb_key}.json' "
                f"(from the `whiteboard_tag` field)."
            )

        return wb_key, file_dict


def get_all_enabled_configurations(
    jbi_folder_path: str = "config/whiteboard_tags/",
) -> Dict[str, Dict]:
    """
        Returns a dictionary key'd by whiteboard_tag
        with value's of associated configuration parameters
    :param jbi_folder_path:
    :return:
    """
    errors = []
    config_map = {}

    for filename in Path(jbi_folder_path).glob("*.json"):
        try:
            filename_s = str(filename)
            if "TEMPLATE" in filename_s:
                continue
            config_key, config_value = process_config_per_file(filename_s)
            if config_key:
                config_map[config_key] = config_value
        except (ValueError, ConfigError) as exception:
            errors.append(exception)

    if errors:
        raise ProcessError("PROCESS ERROR: errors exist.", errors)
    return config_map
