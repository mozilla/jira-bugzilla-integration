"""
Python Module for Pydantic Models and validation
"""
import importlib
from inspect import signature
from types import ModuleType
from typing import Any, Dict, Optional

from pydantic import Extra, ValidationError, root_validator, validator
from pydantic_yaml import YamlModel


class IgnoreInvalidRequestError(Exception):
    """Error thrown when requests are invalid and ignored"""


class ActionError(Exception):
    """Error occurred during Action handling"""


class Action(YamlModel, extra=Extra.allow):
    """
    Action is the inner model for each action in the configuration file"""

    action: str = "src.jbi.whiteboard_actions.default"
    enabled: bool = False
    parameters: dict = {}

    @root_validator
    def validate_action_config(
        cls, values
    ):  # pylint: disable=no-self-argument, no-self-use
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
            raise ValidationError(f"unknown action `{action}`.") from exception
        except (TypeError, AttributeError) as exception:
            raise ValidationError("action is not properly setup.") from exception
        return values


class Actions(YamlModel):
    """
    Actions is the overall model for the list of `actions` in the configuration file
    """

    actions: Dict[str, Action]

    @validator("actions")
    def validate_action_matches_whiteboard(
        cls, actions
    ):  # pylint: disable=no-self-argument, no-self-use
        """
        Validate that the inner actions are named as expected
        """
        if not actions:
            raise ValidationError("no actions configured")
        for name, action in actions.items():
            if name != action.parameters["whiteboard_tag"]:
                raise ValidationError("action name must match whiteboard tag")

        return actions
