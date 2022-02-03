from inspect import getmembers, isfunction, ismodule
from jira-bugzilla-integration.jbi import process as jbi_process

module_dict = {
	jbi_process.key: jbi_process.actions
}

_action_context = {}

def get_action_context_by_key(key):
	global _action_context
	if key not in module_dict:
		assert False, "Unknown key requested"
	if key not in _action_context.keys():
		_action_context[key]=get_action_context_from_module(action_module=module_dict.get(module_dict))
	return _action_context[key]

def get_action_context_from_module(action_module):
	context_map = {}
	for _, module in getmembers(action_module, ismodule):
		print(module)
		methods = getmembers(module, isfunction)
		for method_name, method in methods:
			assert method_name not in context_map.keys(), f"ACTION ERROR: Action with name  `{method_name}` already exists."
	    	context_map[method_name] = method

    return context_map