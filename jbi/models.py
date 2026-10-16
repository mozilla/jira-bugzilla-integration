"""
Python Module for Pydantic Models and validation
"""

import functools
import logging
import warnings
from collections import defaultdict
from copy import copy
from typing import DefaultDict, Literal, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
)

from jbi import Operation, steps
from jbi.bugzilla.models import Bug, BugId, WebhookEvent

logger = logging.getLogger(__name__)

JIRA_HOSTNAMES = ("jira", "atlassian")


class ActionSteps(BaseModel, frozen=True):
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
    attachment: list[str] = [
        "create_comment",
        "maybe_add_phabricator_link",
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

        # Make sure `maybe_update_resolution` comes after `maybe_update_status`.
        try:
            idx_resolution = function_names.index("maybe_update_issue_resolution")
            idx_status = function_names.index("maybe_update_issue_status")
            assert idx_resolution > idx_status, (
                "Step `maybe_update_resolution` should be put after `maybe_update_issue_status`"
            )
        except ValueError:
            # One of these 2 steps not listed.
            pass

        return function_names


class JiraComponents(BaseModel, frozen=True):
    """Controls how Jira components are set on issues in the `maybe_update_components` step."""

    use_bug_component: bool = True
    use_bug_product: bool = False
    use_bug_component_with_product_prefix: bool = False
    create_components: bool = False
    set_custom_components: list[str] = []


class ActionParams(BaseModel, frozen=True):
    """Params passed to Action step functions"""

    jira_project_key: str
    steps: ActionSteps = ActionSteps()
    jira_char_limit: int = 32667
    jira_components: JiraComponents = JiraComponents()
    jira_cf_fx_points_field: str = "customfield_10037"
    jira_severity_field: str = "customfield_10319"
    jira_priority_field: str = "priority"
    jira_resolution_field: str = "resolution"
    labels_brackets: Literal["yes", "no", "both"] = "no"
    status_map: dict[str, str] = {}
    priority_map: dict[str, str] = {
        "": "None",
        "--": "None",
        "P1": "P1",
        "P2": "P2",
        "P3": "P3",
        "P4": "P4",
        "P5": "P5",
    }
    resolution_map: dict[str, str] = {}
    severity_map: dict[str, str | None] = {
        "": None,
        "--": None,
        "S1": "S1",
        "S2": "S2",
        "S3": "S3",
        "S4": "S4",
        "N/A": "N/A",
    }
    cf_fx_points_map: dict[str, int] = {
        "---": 0,
        "": 0,
        "?": 0,
        "1": 1,
        "2": 2,
        "3": 3,
        "5": 5,
        "7": 7,
        "8": 8,
        "12": 12,
        "13": 13,
        "15": 15,
    }
    issue_type_map: dict[str, str] = {"task": "Task", "defect": "Bug"}
    linked_project_excludes: list[str] = ["BZFFX"]

    # --- Bidirectional sync scaffolding (docs/bmo-jira-bidirectional-integration-plan.md) ---
    # These are intentionally no-ops until the deliverables that consume them
    # land (see the plan's Phase 1 D1-D12). Every field defaults so that
    # existing `config.*.yaml` files keep parsing unchanged and existing
    # actions keep their current behavior.

    # R-01: restrict sync to a Product/Component allowlist. `None` means
    # "no restriction", i.e. today's behavior (every bug matching the
    # whiteboard tag is synced, regardless of Product/Component).
    sync_products_components: Optional[list[str]] = None

    # R-04: ignore bugs below a priority/severity threshold. `None` means
    # "no threshold", i.e. today's behavior (every bug is synced regardless
    # of priority/severity).
    min_priority: Optional[str] = None
    min_severity: Optional[str] = None

    # R-11: enable identity-map-based resolution (YAML override + email
    # fallback) for assignee sync and comment attribution. Defaults to off,
    # which preserves today's email-only lookup in `find_jira_user`.
    identity_map_enabled: bool = False

    # Reverse (Jira -> BMO) status/resolution mapping, see plan section 4.1.
    # `status_map` is many-to-one and therefore NOT invertible, so reverse
    # status derives from Jira's built-in status category instead. These two
    # settings only override that default behavior.
    reverse_status_overrides: dict[str, str] = {}
    default_reverse_resolution: Optional[str] = None

    # What to do when an inbound Jira payload carries no changelog, so JBI
    # can see the issue's current state but not which field moved. Default
    # off: "sync only what changed" is what stops a Jira event overwriting a
    # BMO field a human just edited, and without a changelog there is also no
    # previous value for the conflict rule to compare against. Turning this on
    # accepts a full-state push from Jira on every event.
    reverse_sync_without_changelog: bool = False

    # An escape hatch for humans: adding this label to a Jira issue halts
    # syncing for that bug/issue pair in BOTH directions, and removing it
    # resumes. Unset means the feature is off for this action.
    sync_stop_label: Optional[str] = None

    # R-02 / R-03: Jira statuses used by the Phabricator review steps. `None`
    # (the default) means the corresponding step does nothing, so adding the
    # steps to an action's config without setting these is inert.
    phabricator_review_status: Optional[str] = None
    phabricator_changes_requested_status: Optional[str] = None

    # Copying Jira comment *text* onto a bug is gated separately from field
    # sync, and defaults off. A field value is a small, enumerable thing; a
    # comment is free text that may quote an embargoed issue, and publishing
    # it onto a world-readable bug cannot be undone. Enabling this asserts
    # that the Automation rule sends `comment.visibility` and
    # `fields.security`, which the guard needs to fail closed (R-12).
    reverse_comment_sync_enabled: bool = False

    # Enables the Jira -> BMO inbound path (the `/jira_webhook` spine and its
    # reverse field writers) for this action. Defaults to off: no inbound
    # Jira event has any effect until an action opts in.
    jira_inbound_enabled: bool = False

    @field_validator("resolution_map")
    @classmethod
    def validate_resolution_map_is_invertible(cls, value: dict[str, str]):
        """Reject a `resolution_map` that cannot be inverted (plan section 4.1).

        The reverse direction recovers a BMO resolution by inverting this map,
        which is only sound while it is injective. Every `resolution_map` in
        config today is, so this codifies an existing property rather than
        imposing a new one -- and a future many-to-one map fails loudly at
        load instead of silently writing the wrong resolution to a bug.
        """
        seen: dict[str, str] = {}
        collisions = []
        for bmo_resolution, jira_resolution in value.items():
            if jira_resolution in seen:
                collisions.append(
                    f"{jira_resolution!r} <- "
                    f"{seen[jira_resolution]!r} and {bmo_resolution!r}"
                )
            else:
                seen[jira_resolution] = bmo_resolution
        if collisions:
            raise ValueError(
                "`resolution_map` must be invertible for reverse sync, but "
                "these Jira resolutions map from several BMO resolutions: "
                + "; ".join(collisions)
            )
        return value

    @field_validator("reverse_status_overrides")
    @classmethod
    def validate_reverse_status_overrides(cls, value: dict[str, str]):
        """Overrides are keyed by Jira status category, not status name."""
        allowed = {"new", "indeterminate", "done"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                "`reverse_status_overrides` keys must be Jira status "
                f"categories {sorted(allowed)}, got {sorted(unknown)}"
            )
        return value

    @field_validator("min_priority")
    @classmethod
    def validate_min_priority(cls, value: Optional[str]):
        """`min_priority` must be a BMO priority (R-04)."""
        if value is not None and value not in ("P1", "P2", "P3", "P4", "P5"):
            raise ValueError(f"`min_priority` must be one of P1..P5, got {value!r}")
        return value

    @field_validator("min_severity")
    @classmethod
    def validate_min_severity(cls, value: Optional[str]):
        """`min_severity` must be a BMO severity (R-04)."""
        if value is not None and value not in ("S1", "S2", "S3", "S4"):
            raise ValueError(f"`min_severity` must be one of S1..S4, got {value!r}")
        return value


class Action(BaseModel, frozen=True):
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

        return {action.whiteboard_tag: action for action in self.root}

    def __iter__(self):
        return iter(self.root)

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

        return {action.jira_project_key for action in self.root}

    @field_validator("root")
    @classmethod
    def validate_actions(cls, actions: list[Action]):
        """
        Inspect the list of actions:
         - Validate that lookup tags are uniques
         - Ensure we haven't exceeded our maximum configured project limit (see error below)
         - If the action's bugzilla_user_id is "tbd", emit a warning.
        """
        tags = [action.whiteboard_tag.lower() for action in actions]
        duplicated_tags = [t for i, t in enumerate(tags) if t in tags[:i]]
        if duplicated_tags:
            raise ValueError(f"actions have duplicated lookup tags: {duplicated_tags}")

        if len(tags) > 50:
            raise ValueError(
                "The Jira client's `paginated_projects` method assumes we have "
                "up to 50 projects configured. Adjust that implementation before "
                "removing this validation check."
            )

        for action in actions:
            if action.bugzilla_user_id == "tbd":
                warnings.warn(
                    f"Provide bugzilla_user_id data for `{action.whiteboard_tag}` action."
                )

            assert action.parameters.status_map or (
                "maybe_update_issue_status" not in action.parameters.steps.new
                and "maybe_update_issue_status" not in action.parameters.steps.existing
            ), "`maybe_update_issue_status` was used without `status_map`"
            assert action.parameters.resolution_map or (
                "maybe_update_issue_resolution" not in action.parameters.steps.new
                and "maybe_update_issue_resolution"
                not in action.parameters.steps.existing
            ), "`maybe_update_issue_resolution` was used without `resolution_map`"

        return actions

    model_config = ConfigDict(ignored_types=(functools.cached_property,))


class Context(BaseModel, frozen=True):
    """Generic log context throughout JBI"""

    def update(self, **kwargs):
        """Return a copy with updated fields."""
        return self.model_copy(update=kwargs, deep=True)


class JiraContext(Context):
    """Logging context about Jira"""

    project: str
    issue: Optional[str] = None
    labels: Optional[list[str]] = None


class RunnerContext(Context, extra="forbid"):
    """Logging context from runner"""

    operation: Operation
    event: WebhookEvent
    actions: Optional[list[Action]] = None
    bug: BugId | Bug


class ActionContext(Context, extra="forbid"):
    """Logging context from actions"""

    action: Action
    operation: Operation
    current_step: Optional[str] = None
    event: WebhookEvent
    jira: JiraContext
    bug: Bug
    extra: dict[str, str] = {}
    responses_by_step: DefaultDict[str, list] = defaultdict(list)

    def append_responses(self, *responses):
        """Shortcut function to add responses to the existing list."""
        if not self.current_step:
            raise ValueError("`current_step` unset in context.")
        copied = copy(self.responses_by_step)
        copied[self.current_step].extend(responses)
        return self.update(responses_by_step=copied)
