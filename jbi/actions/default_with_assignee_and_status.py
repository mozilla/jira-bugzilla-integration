"""
Extended action that provides some additional features over the default:
  * Updates the Jira assignee when the bug's assignee changes.
  * Optionally updates the Jira status when the bug's resolution or status changes.

`init` is required; and requires at minimum the `jira_project_key` parameter. `status_map` is optional.

`init` should return a __call__able
"""
import logging

from jbi import ActionResult, Operation
from jbi.actions.default import (
    JIRA_REQUIRED_PERMISSIONS as DEFAULT_JIRA_REQUIRED_PERMISSIONS,
)
from jbi.actions.steps import (
    add_link_to_bugzilla,
    add_link_to_jira,
    create_comment,
    create_issue,
    maybe_assign_jira_user,
    maybe_delete_duplicate,
    maybe_update_issue_resolution,
    maybe_update_issue_status,
    update_issue,
)
from jbi.models import ActionContext

logger = logging.getLogger(__name__)


JIRA_REQUIRED_PERMISSIONS = DEFAULT_JIRA_REQUIRED_PERMISSIONS


def init(
    jira_project_key,
    sync_whiteboard_labels=True,
    status_map=None,
    resolution_map=None,
):
    """Function that takes required and optional params and returns a callable object"""
    return AssigneeAndStatusExecutor(
        jira_project_key=jira_project_key,
        sync_whiteboard_labels=sync_whiteboard_labels,
        status_map=status_map or {},
        resolution_map=resolution_map or {},
    )


class AssigneeAndStatusExecutor:
    """Callable class that encapsulates the default_with_assignee_and_status action."""

    def __init__(self, **parameters):
        """Initialize AssigneeAndStatusExecutor Object"""
        self.parameters = parameters

    def __call__(  # pylint: disable=duplicate-code
        self, context: ActionContext
    ) -> ActionResult:
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""

        responses = tuple()  # type: ignore

        steps = {
            Operation.CREATE: [
                create_issue,
                maybe_delete_duplicate,
                add_link_to_bugzilla,
                add_link_to_jira,
                maybe_assign_jira_user,
                maybe_update_issue_resolution,
                maybe_update_issue_status,
            ],
            Operation.UPDATE: [
                update_issue,
                maybe_assign_jira_user,
                maybe_update_issue_resolution,
                maybe_update_issue_status,
            ],
            Operation.COMMENT: [
                create_comment,
            ],
        }
        for step in steps[context.operation]:
            context, step_responses = step(context=context, **self.parameters)
            responses += step_responses

        return True, {"responses": responses}
