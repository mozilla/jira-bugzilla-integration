"""
Python Module for Pydantic Models and validation
"""
import datetime
import functools
import logging
import re
import warnings
from collections import defaultdict
from copy import copy
from typing import DefaultDict, Literal, Mapping, Optional
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel, ConfigDict, Extra, Field, RootModel, field_validator
from typing_extensions import TypedDict

from jbi import Operation, steps
from jbi.errors import ActionNotFoundError

logger = logging.getLogger(__name__)

JIRA_HOSTNAMES = ("jira", "atlassian")


class ActionSteps(BaseModel):
    """Step functions to run for each type of Bugzilla webhook payload"""

    new: list[str] = [
        "create_issue",
        "maybe_delete_duplicate",
        "add_link_to_bugzilla",
        "add_link_to_jira",
        "sync_whiteboard_labels",
    ]
    existing: list[str] = [
        "update_issue_summary",
        "sync_whiteboard_labels",
        "add_jira_comments_for_changes",
    ]
    comment: list[str] = [
        "create_comment",
    ]

    @field_validator("*")
    @classmethod
    def validate_steps(cls, function_names: list[str]):
        """Validate that all configure step functions exist in the steps module"""

        invalid_functions = [
            func_name for func_name in function_names if not hasattr(steps, func_name)
        ]
        if invalid_functions:
            raise ValueError(
                f"The following functions are not available in the `steps` module: {', '.join(invalid_functions)}"
            )
        return function_names


class JiraComponents(BaseModel):
    """Controls how Jira components are set on issues in the `maybe_update_components` step."""

    use_bug_component: bool = True
    use_bug_product: bool = False
    use_bug_component_with_product_prefix: bool = False
    set_custom_components: list[str] = []


class ActionParams(BaseModel):
    """Params passed to Action step functions"""

    jira_project_key: str
    steps: ActionSteps = ActionSteps()
    jira_components: JiraComponents = JiraComponents()
    labels_brackets: Literal["yes", "no", "both"] = "no"
    status_map: dict[str, str] = {}
    resolution_map: dict[str, str] = {}
    issue_type_map: dict[str, str] = {"task": "Task", "defect": "Bug"}


class Action(BaseModel):
    """
    Action is the inner model for each action in the configuration file"""

    whiteboard_tag: str
    bugzilla_user_id: int | list[int] | Literal["tbd"]
    description: str
    enabled: bool = True
    parameters: ActionParams

    @property
    def jira_project_key(self):
        """Return the configured project key."""
        return self.parameters.jira_project_key


class Actions(RootModel):
    """
    Actions is the container model for the list of actions in the configuration file
    """

    root: list[Action] = Field(..., min_length=1)

    @functools.cached_property
    def by_tag(self) -> Mapping[str, Action]:
        """Build mapping of actions by lookup tag."""
        # pylint: disable-next=not-an-iterable
        return {action.whiteboard_tag: action for action in self.root}

    def __iter__(self):
        return iter(self.root)  # pylint: disable=not-an-iterable

    def __len__(self):
        return len(self.root)

    def __getitem__(self, item):
        return self.by_tag[item]

    def get(self, tag: Optional[str]) -> Optional[Action]:
        """Lookup actions by whiteboard tag"""
        return self.by_tag.get(tag.lower()) if tag else None

    @functools.cached_property
    def configured_jira_projects_keys(self) -> set[str]:
        """Return the list of Jira project keys from all configured actions"""
        # pylint: disable-next=not-an-iterable
        return {action.jira_project_key for action in self.root}

    @field_validator("root")
    @classmethod
    def validate_actions(cls, actions: list[Action]):
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

    model_config = ConfigDict(ignored_types=(functools.cached_property,))


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
    time: Optional[datetime.datetime] = None
    user: Optional[BugzillaWebhookUser] = None
    changes: Optional[list[BugzillaWebhookEventChange]] = None
    target: Optional[str] = None
    routing_key: Optional[str] = None

    def changed_fields(self) -> list[str]:
        """Returns the names of changed fields in a bug"""
        # pylint: disable-next=not-an-iterable
        return [c.field for c in self.changes] if self.changes else []


class BugzillaWebhookComment(BaseModel):
    """Bugzilla Comment Object"""

    body: Optional[str] = None
    id: Optional[int] = None
    number: Optional[int] = None
    is_private: Optional[bool] = None
    creation_time: Optional[datetime.datetime] = None


class BugzillaBug(BaseModel):
    """Bugzilla Bug Object"""

    id: int
    is_private: Optional[bool] = None
    type: Optional[str] = None
    product: Optional[str] = None
    component: Optional[str] = None
    whiteboard: Optional[str] = None
    keywords: Optional[list] = None
    flags: Optional[list] = None
    groups: Optional[list] = None
    status: Optional[str] = None
    resolution: Optional[str] = None
    see_also: Optional[list] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    priority: Optional[str] = None
    creator: Optional[str] = None
    assigned_to: Optional[str] = None
    comment: Optional[BugzillaWebhookComment] = None

    @property
    def product_component(self) -> str:
        """Return the component prefixed with the product
        as show in the Bugzilla UI (eg. ``Core::General``).
        """
        result = self.product + "::" if self.product else ""
        return result + self.component if self.component else result

    def is_assigned(self) -> bool:
        """Return `true` if the bug is assigned to a user."""
        return self.assigned_to != "nobody@mozilla.org"

    def extract_from_see_also(self, project_key):
        """Extract Jira Issue Key from see_also if jira url present"""
        if not self.see_also or len(self.see_also) == 0:
            return None

        candidates = []
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
                    # Issue keys are like `{project_key}-{number}`
                    if parsed_jira_key.startswith(f"{project_key}-"):
                        return parsed_jira_key
                    # If not obvious, then keep this link as candidate.
                    candidates.append(parsed_jira_key)

        return candidates[0] if candidates else None

    def lookup_action(self, actions: Actions) -> Action:
        """
        Find first matching action from bug's whiteboard field.

        Tags are strings between brackets and can have prefixes/suffixes
        using dashes (eg. ``[project]``, ``[project-moco]``, ``[project-moco-sprint1]``).
        """
        if self.whiteboard:
            for tag, action in actions.by_tag.items():
                # [tag-word], [tag-], [tag], but not [word-tag] or [tagword]
                search_string = r"\[" + tag + r"(-[^\]]*)*\]"
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

    faults: Optional[list] = None
    bugs: Optional[list[BugzillaBug]] = None


class BugzillaWebhook(BaseModel):
    """Bugzilla Webhook"""

    id: int
    name: str
    url: str
    event: str
    product: str
    component: str
    enabled: bool
    errors: int
    # Ignored fields:
    # creator: str

    @property
    def slug(self):
        """Return readable identifier"""
        name = self.name.replace(" ", "-").lower()
        product = self.product.replace(" ", "-").lower()
        return f"{self.id}-{name}-{product}"


class BugzillaWebhooksResponse(BaseModel):
    """Bugzilla Webhooks List Response Object"""

    webhooks: Optional[list[BugzillaWebhook]] = None


class Context(BaseModel):
    """Generic log context throughout JBI"""

    def update(self, **kwargs):
        """Return a copy with updated fields."""
        return self.copy(update=kwargs)


class JiraContext(Context):
    """Logging context about Jira"""

    project: str
    issue: Optional[str] = None
    labels: Optional[list[str]] = None


BugId = TypedDict("BugId", {"id": Optional[int]})


class RunnerContext(Context, extra=Extra.forbid):
    """Logging context from runner"""

    rid: str
    operation: Operation
    event: BugzillaWebhookEvent
    action: Optional[Action] = None
    bug: BugId | BugzillaBug


class ActionContext(Context, extra=Extra.forbid):
    """Logging context from actions"""

    action: Action
    rid: str
    operation: Operation
    current_step: Optional[str] = None
    event: BugzillaWebhookEvent
    jira: JiraContext
    bug: BugzillaBug
    extra: dict[str, str] = {}
    responses_by_step: DefaultDict[str, list] = defaultdict(list)

    def append_responses(self, *responses):
        """Shortcut function to add responses to the existing list."""
        if not self.current_step:
            raise ValueError("`current_step` unset in context.")
        copied = copy(self.responses_by_step)
        copied[self.current_step].extend(responses)
        return self.update(responses_by_step=copied)
