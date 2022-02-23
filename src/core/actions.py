from inspect import getmembers, isfunction, ismodule
from typing import Dict

module_dict: Dict = {}
# update this dict with additional action contexts


class ActionError(Exception):
    pass


def get_action_context_by_key(key):
    if key not in module_dict:
        raise ValueError("Unknown key requested")
    requested_module = module_dict.get(key)
    return get_action_context_from_module(action_module=requested_module)


def get_action_context_from_module(action_module):
    context_map = {}
    for _, module in getmembers(action_module, ismodule):
        methods = getmembers(module, isfunction)
        for method_name, method in methods:
            method_name_available = (
                method_name
                not in context_map.keys()  # pylint: disable=consider-iterating-dictionary
            )
            if not method_name_available:
                raise ActionError(
                    f"ACTION ERROR: Action with name  `{method_name}` already exists."
                )
            context_map[method_name] = method

    return context_map
