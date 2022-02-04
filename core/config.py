import json
import os
import glob
from typing import Dict, List, Tuple

from core import actions


def per_file_process(filename: str, ret_dict: dict, required_keys, action_key, filename_key="") -> Tuple:
    file = open(filename)
    data = json.load(filename)
    file.close()
    ret_dict.update(data)

    action = ret_dict.get("action")
    known_actions = actions.get_action_context_by_key(key=action_key)
    if action not in known_actions:
        assert False, f"CONFIG ERROR: Unknown action `{action}`."

    if not ret_dict.get("enabled"):
        # Only process enabled files
        return None, None

    for key_str in required_keys:
        key_value = ret_dict.get(key_str)
        assert key_value is not None, f"CONFIG ERROR: Required field `{key_str}` not found."

    ret_key = ret_dict.get(filename_key)
    assert (ret_key is not None and ret_key in filename) or (
            filename_key == ""), f"CONFIG ERROR: Filename should contain value within key `{filename_key}`. The " \
                                 f"value {ret_key} from the key is expected to be in the filename. "

    return ret_key, ret_dict


def process_all_files_in_path(
        process,
        folder_path,
        errors: List = [],
        config_map: Dict = {}
):
    # Run `process` on all files within folder_path.
    # Aggregate errors; fail on blocking errors.
    # returns dict of config
    for filename in glob.glob(os.path.join(folder_path, '*.json')):
        try:
            key, value = process(filename)
            if key:
                config_map[key] = value
        except Exception as e:
            errors.append(e)

    if errors:
        # Fail.
        assert False
    return config_map
