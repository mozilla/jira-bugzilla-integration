"""
Python Module for Pydantic Models and validation
"""
import importlib
from inspect import signature
from types import ModuleType
from typing import Any, Dict, Optional

from pydantic import Extra, ValidationError, root_validator, validator
from pydantic_yaml import YamlModel


class ActionConfig(YamlModel, extra=Extra.allow):
    """
    ActionConfig is the inner model of `actions` in yaml
    """

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
    Actions is the overall model from parsing the yaml config `actions` sections
    """

    actions: Dict[str, ActionConfig]

    @validator("actions")
    def validate_action_yaml_jbi_naming(
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
