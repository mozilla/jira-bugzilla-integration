"""
Python Module for Pydantic Models and validation
"""
import datetime
import functools
import importlib
import logging
import re
import warnings
from inspect import signature
from types import ModuleType
from typing import Callable, Literal, Mapping, Optional, TypedDict
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel, Extra, Field, PrivateAttr, root_validator, validator
from pydantic_yaml import YamlModel

from jbi import Operation
from jbi.errors import ActionNotFoundError

logger = logging.getLogger(__name__)

JIRA_HOSTNAMES = ("jira", "atlassian")


class ActionParameters(BaseModel):
    """Action parameters"""

    # For runner
    jira_project_key: str
    steps: Optional[dict[str, list[str]]] = None
    # For steps
    status_map: Optional[dict[str, str]] = None
    resolution_map: Optional[dict[str, str]] = None
    jira_components: Optional[list[str]] = None
    sync_whiteboard_labels: bool = True


class Action(YamlModel):
    """
    Action is the inner model for each action in the configuration file"""

    whiteboard_tag: str
    module: str = "jbi.actions.default"
    bugzilla_user_id: int | list[int] | Literal["tbd"]
    description: str
    enabled: bool = True
    allow_private: bool = False
    parameters: ActionParameters
    _caller: Optional[Callable] = PrivateAttr(default=None)
    _required_jira_permissions: set[str] = PrivateAttr(default=None)

    @property
    def jira_project_key(self):
        """Return the configured project key."""
        return self.parameters.jira_project_key

    @property
    def caller(self) -> Callable:
        """Return the initialized callable for this action."""
        if self._caller is None:
            action_module: ModuleType = importlib.import_module(self.module)
            initialized: Callable = action_module.init(**self.parameters.dict())  # type: ignore
            self._caller = initialized
        return self._caller

    @property
    def required_jira_permissions(self) -> set[str]:
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
            try:
                action_parameters = values["parameters"].dict()
            except KeyError:
                action_parameters = {}
            action_module: ModuleType = importlib.import_module(module)
            if not action_module:
                raise TypeError("Module is not found.")
            if not hasattr(action_module, "init"):
                raise TypeError("Module is missing `init` method.")

            signature(action_module.init).bind(**action_parameters)
        except ImportError as exception:
            raise ValueError(f"unknown Python module `{module}`.") from exception
        except (TypeError, AttributeError) as exception:
            raise ValueError(
                f"action '{module}' is not properly setup. {exception}"
            ) from exception
        return values


class Actions(YamlModel):
    """
    Actions is the container model for the list of actions in the configuration file
    """

    __root__: list[Action] = Field(..., min_items=1)

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
    def configured_jira_projects_keys(self) -> set[str]:
        """Return the list of Jira project keys from all configured actions"""
        return {action.jira_project_key for action in self.__root__}

    @validator("__root__")
    def validate_actions(  # pylint: disable=no-self-argument
        cls, actions: list[Action]
    ):
        """
        Inspect the list of actions:
         - Validate that lookup tags are uniques
         - If the action's bugzilla_user_id is "tbd", emit a warning.
        """
        tags = [action.whiteboard_tag.lower() for action in actions]
        duplicated_tags = [t for i, t in enumerate(tags) if t in tags[:i]]
        if duplicated_tags:
            raise ValueError(f"actions have duplicated lookup tags: {duplicated_tags}")

        for action in actions:
            if action.bugzilla_user_id == "tbd":
                warnings.warn(
                    f"Provide bugzilla_user_id data for `{action.whiteboard_tag}` action."
                )

        return actions

    class Config:
        """Pydantic configuration"""

        keep_untouched = (functools.cached_property,)


class BugzillaWebhookUser(BaseModel):
    """Bugzilla User Object"""

    id: int
    login: str
    real_name: str


class BugzillaWebhookEventChange(BaseModel):
    """Bugzilla Change Object"""

    field: str
    removed: str
    added: str


class BugzillaWebhookEvent(BaseModel):
    """Bugzilla Event Object"""

    action: str
    time: Optional[datetime.datetime]
    user: Optional[BugzillaWebhookUser]
    changes: Optional[list[BugzillaWebhookEventChange]]
    target: Optional[str]
    routing_key: Optional[str]

    def changed_fields(self) -> list[str]:
        """Returns the names of changed fields in a bug"""
        if self.changes:
            return [c.field for c in self.changes]

        # Private bugs don't include the changes field in the event, but the
        # field names are in the routing key.
        if self.routing_key is not None and self.routing_key[0:11] == "bug.modify:":
            return self.routing_key[11:].split(",")

        return []


class BugzillaWebhookAttachment(BaseModel):
    """Bugzilla Attachment Object"""

    content_type: Optional[str]
    creation_time: Optional[datetime.datetime]
    description: Optional[str]
    file_name: Optional[str]
    flags: Optional[list]
    id: int
    is_obsolete: Optional[bool]
    is_patch: Optional[bool]
    is_private: Optional[bool]
    last_change_time: Optional[datetime.datetime]


class BugzillaWebhookComment(BaseModel):
    """Bugzilla Comment Object"""

    body: Optional[str]
    id: Optional[int]
    number: Optional[int]
    is_private: Optional[bool]
    creation_time: Optional[datetime.datetime]


class BugzillaBug(BaseModel):
    """Bugzilla Bug Object"""

    id: int
    is_private: Optional[bool]
    type: Optional[str]
    product: Optional[str]
    component: Optional[str]
    whiteboard: Optional[str]
    keywords: Optional[list]
    flags: Optional[list]
    groups: Optional[list]
    status: Optional[str]
    resolution: Optional[str]
    see_also: Optional[list]
    summary: Optional[str]
    severity: Optional[str]
    priority: Optional[str]
    creator: Optional[str]
    assigned_to: Optional[str]
    comment: Optional[BugzillaWebhookComment]

    def is_assigned(self) -> bool:
        """Return `true` if the bug is assigned to a user."""
        return self.assigned_to != "nobody@mozilla.org"

    def get_whiteboard_as_list(self) -> list[str]:
        """Convert string whiteboard into list, splitting on ']' and removing '['."""
        split_list = (
            self.whiteboard.replace("[", "").split("]") if self.whiteboard else []
        )
        return [x.strip() for x in split_list if x not in ["", " "]]

    def get_jira_labels(self) -> list[str]:
        """
        whiteboard labels are added as a convenience for users to search in jira;
        bugzilla is an expected label in Jira
        since jira-labels can't contain a " ", convert to "."
        """
        wb_list = self.get_whiteboard_as_list()
        wb_bracket_list = [f"[{wb}]" for wb in wb_list]
        without_spaces = [wb.replace(" ", ".") for wb in (wb_list + wb_bracket_list)]
        return ["bugzilla"] + without_spaces

    def issue_type(self) -> str:
        """Get the Jira issue type for this bug"""
        type_map: dict = {"enhancement": "Task", "task": "Task", "defect": "Bug"}
        return type_map.get(self.type, "Task")

    def extract_from_see_also(self):
        """Extract Jira Issue Key from see_also if jira url present"""
        if not self.see_also or len(self.see_also) == 0:
            return None

        for url in self.see_also:  # pylint: disable=not-an-iterable
            try:
                parsed_url: ParseResult = urlparse(url=url)
                host_parts = parsed_url.hostname.split(".")
            except (ValueError, AttributeError):
                logger.debug(
                    "Bug %s `see_also` is not a URL: %s",
                    self.id,
                    url,
                    extra={
                        "bug": {
                            "id": self.id,
                        }
                    },
                )
                continue

            if any(part in JIRA_HOSTNAMES for part in host_parts):
                parsed_jira_key = parsed_url.path.rstrip("/").split("/")[-1]
                if parsed_jira_key:  # URL ending with /
                    return parsed_jira_key

        return None

    def lookup_action(self, actions: Actions) -> Action:
        """Find first matching action from bug's whiteboard list"""
        if self.whiteboard:
            for tag, action in actions.by_tag.items():
                search_string = r"\[[^\]]*" + tag + r"[^\]]*\]"
                if re.search(search_string, self.whiteboard, flags=re.IGNORECASE):
                    return action

        raise ActionNotFoundError(", ".join(actions.by_tag.keys()))


class BugzillaWebhookRequest(BaseModel):
    """Bugzilla Webhook Request Object"""

    rid: str = ""  # This field has a default since it's not parsed from body.
    webhook_id: int
    webhook_name: str
    event: BugzillaWebhookEvent
    bug: BugzillaBug


class BugzillaComment(BaseModel):
    """Bugzilla Comment"""

    id: int
    text: str
    is_private: bool
    creator: str


class BugzillaApiResponse(BaseModel):
    """Bugzilla Response Object"""

    faults: Optional[list]
    bugs: Optional[list[BugzillaBug]]


class BugzillaWebhook(BaseModel):
    """Bugzilla Webhook"""

    id: int
    creator: str
    name: str
    url: str
    event: str
    product: str
    component: str
    enabled: bool
    errors: int


class BugzillaWebhooksResponse(BaseModel):
    """Bugzilla Webhooks List Response Object"""

    webhooks: Optional[list[BugzillaWebhook]]


class Context(BaseModel):
    """Generic log context throughout JBI"""

    def update(self, **kwargs):
        """Return a copy with updated fields."""
        return self.copy(update=kwargs)


class JiraContext(Context):
    """Logging context about Jira"""

    project: str
    issue: Optional[str]


BugId = TypedDict("BugId", {"id": Optional[int]})


class RunnerContext(Context, extra=Extra.forbid):
    """Logging context from runner"""

    rid: str
    operation: Operation
    event: BugzillaWebhookEvent
    action: Optional[Action]
    bug: BugId | BugzillaBug


class ActionContext(Context, extra=Extra.forbid):
    """Logging context from actions"""

    rid: str
    operation: Operation
    event: BugzillaWebhookEvent
    jira: JiraContext
    bug: BugzillaBug
    extra: dict[str, str] = {}
