import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core import actions


class ConfigError(Exception):
    pass


class ProcessError(Exception):
    pass


def per_file_process(
    filename: str, ret_dict: Dict, required_keys, action_key, filename_key
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    with open(filename, encoding="utf-8") as file:
        data = json.load(file)
        ret_dict.update(data)
        if not ret_dict.get("enabled"):
            # Only process enabled files
            return None, None

        action = ret_dict.get("action")
        known_actions = actions.get_action_context_by_key(key=action_key)
        if action not in known_actions:
            raise ConfigError(f"CONFIG ERROR: Unknown action `{action}`.")

        for key_str in required_keys:
            key_value = ret_dict.get(key_str)
            if not key_value:
                raise ConfigError(
                    f"CONFIG ERROR: Required field `{key_str}` not found in {filename}."
                )

        ret_key = ret_dict.get(filename_key)
        filename_valid = ret_key is not None and ret_key in filename
        if not filename_valid:
            raise ConfigError(
                f"CONFIG ERROR: Filename should contain value within key `{filename_key}`. The "
                f"value {ret_key} from the key is expected to be in the filename. "
            )

        return ret_key, ret_dict


def process_all_files_in_path(
    process, folder_path, errors: List = None, config_map: Dict = None
):
    # Run `process` on all files within folder_path.
    # Aggregate errors; fail on blocking errors.
    # returns dict of config
    if not errors:
        errors = []
    if not config_map:
        config_map = {}

    for filename in Path(folder_path).glob("*.json"):
        try:
            filename_s = str(filename)
            if "TEMPLATE" in filename_s:
                continue
            key, value = process(filename_s)
            if key:
                config_map[key] = value
        except (ValueError, ConfigError, actions.ActionError) as exception:
            errors.append(exception)

    if errors:
        raise ProcessError(f"PROCESS ERROR: errors exist: {errors}")
    return config_map
