"""
Python Module for Pydantic Models and validation
"""
import functools
import importlib
import warnings
from inspect import signature
from types import ModuleType
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Union

from pydantic import EmailStr, Extra, root_validator, validator
from pydantic_yaml import YamlModel


class Action(YamlModel):
    """
    Action is the inner model for each action in the configuration file"""

    action: str = "src.jbi.whiteboard_actions.default"
    # TODO: Remove the tbd literal option when all actions have contact info # pylint: disable=fixme
    contact: Union[EmailStr, List[EmailStr], Literal["tbd"]]
    description: str
    enabled: bool = False
    allow_private: bool = False
    parameters: dict = {}

    @functools.cached_property
    def callable(self) -> Callable:
        """Return the initialized callable for this action."""
        action_module: ModuleType = importlib.import_module(self.action)
        initialized: Callable = action_module.init(**self.parameters)  # type: ignore
        return initialized

    @root_validator
    def validate_action_config(  # pylint: disable=no-self-argument
        cls, values
    ):
        """Validate action: exists, has init function, and has expected params"""
        try:
            action: str = values["action"]  # type: ignore
            action_parameters: Optional[Dict[str, Any]] = values["parameters"]
            action_module: ModuleType = importlib.import_module(action)
            if not action_module:
                raise TypeError("Module is not found.")
            if not hasattr(action_module, "init"):
                raise TypeError("Module is missing `init` method.")

            signature(action_module.init).bind(**action_parameters)  # type: ignore
        except ImportError as exception:
            raise ValueError(f"unknown action `{action}`.") from exception
        except (TypeError, AttributeError) as exception:
            raise ValueError("action is not properly setup.") from exception
        return values

    class Config:
        """Pydantic configuration"""

        extra = Extra.allow
        keep_untouched = (functools.cached_property,)


class Actions(YamlModel):
    """
    Actions is the container model for the list of actions in the configuration file
    """

    __root__: Mapping[str, Action]

    def __len__(self):
        return len(self.__root__)

    def __getitem__(self, item):
        return self.__root__[item]

    def get(self, tag: Optional[str]) -> Optional[Action]:
        """Lookup actions by whiteboard tag"""
        return self.__root__.get(tag.lower()) if tag else None

    @validator("__root__")
    def validate_actions(  # pylint: disable=no-self-argument
        cls, actions: Mapping[str, Action]
    ):
        """
        Inspect action values with the context of the top-level key and:
         - Validate that the inner actions are named as expected (key matches
           whiteboard tag parameter)
         - If the action's contact is "tbd", emit a warning.
        """
        if not actions:
            raise ValueError("no actions configured")
        for name, action in actions.items():
            if name.lower() != action.parameters["whiteboard_tag"]:
                raise ValueError("action name must match whiteboard tag")
            if action.contact == "tbd":
                warnings.warn(f"Provide contact data for `{name}` action.")

        return actions
