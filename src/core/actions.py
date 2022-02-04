from inspect import getmembers, isfunction, ismodule

from src.jbi import process as jbi_process

module_dict = {jbi_process.ACTION_KEY: jbi_process.actions}


def get_action_context_by_key(key):
    if key not in module_dict:
        assert False, "Unknown key requested"
    return get_action_context_from_module(action_module=module_dict.get(module_dict))


def get_action_context_from_module(action_module):
    context_map = {}
    for _, module in getmembers(action_module, ismodule):
        print(module)
        methods = getmembers(module, isfunction)
        for method_name, method in methods:
            assert (
                method_name
                not in context_map.keys()  # pylint: disable=consider-iterating-dictionary
            ), f"ACTION ERROR: Action with name  `{method_name}` already exists."
            context_map[method_name] = method

    return context_map
