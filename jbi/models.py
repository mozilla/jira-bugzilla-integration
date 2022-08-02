"""
Python Module for Pydantic Models and validation
"""
import functools
import importlib
import warnings
from inspect import signature
from types import ModuleType
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Set, Union

from pydantic import EmailStr, Field, PrivateAttr, root_validator, validator
from pydantic_yaml import YamlModel


class Action(YamlModel):
    """
    Action is the inner model for each action in the configuration file"""

    whiteboard_tag: str
    module: str = "jbi.actions.default"
    # TODO: Remove the tbd literal option when all actions have contact info # pylint: disable=fixme
    contact: Union[EmailStr, List[EmailStr], Literal["tbd"]]
    description: str
    enabled: bool = False
    allow_private: bool = False
    parameters: dict = {}
    _caller: Callable = PrivateAttr(default=None)
    _required_jira_permissions: Set[str] = PrivateAttr(default=None)

    @property
    def caller(self) -> Callable:
        """Return the initialized callable for this action."""
        if not self._caller:
            action_module: ModuleType = importlib.import_module(self.module)
            initialized: Callable = action_module.init(**self.parameters)  # type: ignore
            self._caller = initialized
        return self._caller

    @property
    def required_jira_permissions(self) -> Set[str]:
        """Return the required Jira permissions for this action to be executed."""
        if not self._required_jira_permissions:
            action_module: ModuleType = importlib.import_module(self.module)
            perms = getattr(action_module, "JIRA_REQUIRED_PERMISSIONS")
            self._required_jira_permissions = perms
        return self._required_jira_permissions

    @root_validator
    def validate_action_config(cls, values):  # pylint: disable=no-self-argument
        """Validate action: exists, has init function, and has expected params"""
        try:
            module: str = values["module"]  # type: ignore
            action_parameters: Optional[Dict[str, Any]] = values["parameters"]
            action_module: ModuleType = importlib.import_module(module)
            if not action_module:
                raise TypeError("Module is not found.")
            if not hasattr(action_module, "init"):
                raise TypeError("Module is missing `init` method.")

            signature(action_module.init).bind(**action_parameters)  # type: ignore
        except ImportError as exception:
            raise ValueError(f"unknown Python module `{module}`.") from exception
        except (TypeError, AttributeError) as exception:
            raise ValueError(f"action is not properly setup.{exception}") from exception
        return values


class Actions(YamlModel):
    """
    Actions is the container model for the list of actions in the configuration file
    """

    __root__: List[Action] = Field(..., min_items=1)

    @functools.cached_property
    def by_tag(self) -> Mapping[str, Action]:
        """Build mapping of actions by lookup tag."""
        return {action.whiteboard_tag: action for action in self.__root__}

    def __iter__(self):
        return iter(self.__root__)

    def __len__(self):
        return len(self.__root__)

    def __getitem__(self, item):
        return self.by_tag[item]

    def get(self, tag: Optional[str]) -> Optional[Action]:
        """Lookup actions by whiteboard tag"""
        return self.by_tag.get(tag.lower()) if tag else None

    @functools.cached_property
    def configured_jira_projects_keys(self) -> Set[str]:
        """Return the list of Jira project keys from all configured actions"""
        return {
            action.parameters["jira_project_key"]
            for action in self.__root__
            if "jira_project_key" in action.parameters
        }

    @validator("__root__")
    def validate_actions(  # pylint: disable=no-self-argument
        cls, actions: List[Action]
    ):
        """
        Inspect the list of actions:
         - Validate that lookup tags are uniques
         - If the action's contact is "tbd", emit a warning.
        """
        tags = [action.whiteboard_tag.lower() for action in actions]
        duplicated_tags = [t for i, t in enumerate(tags) if t in tags[:i]]
        if duplicated_tags:
            raise ValueError(f"actions have duplicated lookup tags: {duplicated_tags}")

        for action in actions:
            if action.contact == "tbd":
                warnings.warn(
                    f"Provide contact data for `{action.whiteboard_tag}` action."
                )

        return actions

    class Config:
        """Pydantic configuration"""

        keep_untouched = (functools.cached_property,)
