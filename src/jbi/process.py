from src.core import configurator
from src.jbi import whiteboard_actions

ACTION_KEY = "jbi"
actions = whiteboard_actions
JBI_FOLDER_PATH = "src/jbi/whiteboard_tags/"


def jbi_config_process(filename: str):
    default_dict = {"action": "default_action", "enabled": True}
    filename_key = "whiteboard_tag"
    req_keys = ["jira_project_key", filename_key]
    return configurator.per_file_process(
        filename,
        ret_dict=default_dict,
        required_keys=req_keys,
        filename_key=filename_key,
        action_key=ACTION_KEY,
    )


def jbi_config_map():
    return configurator.process_all_files_in_path(
        folder_path=JBI_FOLDER_PATH, process=jbi_config_process
    )
