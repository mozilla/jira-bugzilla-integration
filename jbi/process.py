from config import per_file_process, process_all_files_in_path
from jira-bugzilla-integration.jbi import whiteboard_actions

action_key ="jbi"
actions = whiteboard_actions
jbi_folder_path = "jira-bugzilla-integration/jbi/whiteboard_tags/"

def jbi_config_process(filename: str):
	default_dict = {"action":"default_action","enabled":True}
	filename_key="whiteboard_tag"
	req_keys = ["jira_project_key", filename_key]
	jbi_action_key ="jbi"
	return per_file_process(
		filename,
		ret_dict =default_dict ,
		required_keys = req_keys,
		filename_key=filename_key,
		action_key=jbi_action_key
		)

def jbi_config_map():
	return process_all_files_in_path(
		folder_path=jbi_folder_path,
		process=jbi_config_process
	)