import importlib
from types import ModuleType
from typing import Dict

action_list = [
    "src.jbi.whiteboard_actions.default",
    "src.jbi.whiteboard_actions.example",
]

# update `action_list` with additional action modules

module_dict: Dict[str, ModuleType] = {
    action: importlib.import_module(action) for action in action_list
}
