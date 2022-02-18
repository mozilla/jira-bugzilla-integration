import glob
import json
import os
from typing import Dict, List, Tuple

from src.core import actions


def per_file_process(
    filename: str, ret_dict: dict, required_keys, action_key, filename_key
) -> Tuple:
    with open(filename, encoding="utf-8") as file:
        data = json.load(file)
        ret_dict.update(data)
        if not ret_dict.get("enabled"):
            # Only process enabled files
            return None, None

        action = ret_dict.get("action")
        known_actions = actions.get_action_context_by_key(key=action_key)
        if action not in known_actions:
            assert False, f"CONFIG ERROR: Unknown action `{action}`."

        for key_str in required_keys:
            key_value = ret_dict.get(key_str)
            assert (
                key_value is not None
            ), f"CONFIG ERROR: Required field `{key_str}` not found in {filename}."

        ret_key = ret_dict.get(filename_key)
        assert ret_key is not None and ret_key in filename, (
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

    for filename in glob.glob(os.path.join(folder_path, "*.json")):
        try:
            if "TEMPLATE" in filename:
                continue
            key, value = process(filename)
            if key:
                config_map[key] = value
        except Exception as exception:  # pylint: disable=broad-except
            errors.append(exception)

    if errors:
        assert False, f"PROCESS ERROR: errors exist: {errors}"
    return config_map
