from src.app import environment
from src.core import configurator
from src.jbi import whiteboard_actions

settings = environment.get_settings()


def jbi_config_process(filename: str):
    default_dict = {
        "action": "default_action",
        "enabled": True,
    }  # Could be migrated to a json file
    filename_key = "whiteboard_tag"
    req_keys = ["jira_project_key", filename_key]
    return configurator.per_file_process(
        filename,
        ret_dict=default_dict,
        required_keys=req_keys,
        filename_key=filename_key,
        action_key=settings.jbi_action_key,
    )


def jbi_config_map():
    return configurator.process_all_files_in_path(
        folder_path=settings.jbi_folder_path, process=jbi_config_process
    )


def jbi_action_map():
    return {settings.jbi_action_key: whiteboard_actions}
