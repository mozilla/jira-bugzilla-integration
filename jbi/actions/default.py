"""
The `default` action takes a list of steps from configuration and executes them
in chain.

The `runner` will call this action with an initialized context. When a Bugzilla ticket
is created or updated, its `operation` attribute will be `Operation.CREATE` or `Operation.UPDATE`,
and when a comment is posted, it will be set to `Operation.COMMENT`.
"""
import logging
from typing import Callable, Optional

from jbi import ActionResult, Operation
from jbi.errors import IncompleteStepError
from jbi.actions import steps as steps_module
from jbi.environment import get_settings
from jbi.models import ActionContext

settings = get_settings()

logger = logging.getLogger(__name__)

JIRA_REQUIRED_PERMISSIONS = {
    "ADD_COMMENTS",
    "CREATE_ISSUES",
    "DELETE_ISSUES",
    "EDIT_ISSUES",
}

DEFAULT_STEPS = {
    "new": [
        "create_issue",
        "maybe_delete_duplicate",
        "add_link_to_bugzilla",
        "add_link_to_jira",
        "sync_whiteboard_labels",
    ],
    "existing": [
        "update_issue_summary",
        "sync_whiteboard_labels",
        "add_jira_comments_for_changes",
    ],
    "comment": [
        "create_comment",
    ],
}


def groups2operation(steps):
    """In the configuration files, the steps are grouped by `new`, `existing`,
    and `comment`. Internally, this correspond to enums of `Operation`.
    This helper remaps the list of steps.
    """
    group_to_operation = {
        "new": Operation.CREATE,
        "existing": Operation.UPDATE,
        "comment": Operation.COMMENT,
    }
    try:
        by_operation = {
            group_to_operation[entry]: steps_list for entry, steps_list in steps.items()
        }
    except KeyError as err:
        raise ValueError(f"Unsupported entry in `steps`: {err}") from err
    return by_operation


def init(
    jira_project_key,
    steps: Optional[dict[str, list[str]]] = None,
    **kwargs,
):
    """Function that takes required and optional params and returns a callable object"""
    # Merge specified steps with default ones.
    steps = {**DEFAULT_STEPS, **(steps or {})}

    steps_by_operation = groups2operation(steps)

    # Turn the steps strings into references to functions of the `jbi.actions.steps` module.
    steps_callables = {
        group: [getattr(steps_module, step_str) for step_str in steps_list]
        for group, steps_list in steps_by_operation.items()
    }

    return Executor(jira_project_key=jira_project_key, steps=steps_callables, **kwargs)


class Executor:
    """Callable class that encapsulates the default action."""

    def __init__(self, steps: dict[Operation, list[Callable]], **parameters):
        """Initialize Executor Object"""
        self.steps = steps
        self.parameters = parameters

    def __call__(self, context: ActionContext) -> ActionResult:
        """Called from `runner` when the action is used."""
        for step in self.steps[context.operation]:
            try:
                context = step(context=context, **self.parameters)
            except IncompleteStepError as exc:
                context = exc.context

        for response in context.responses:
            logger.debug(
                "Received %s",
                response,
                extra={
                    "response": response,
                    **context.dict(),
                },
            )

        return True, {"responses": context.responses}
