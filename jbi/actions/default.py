"""
Default action is listed below.
`init` is required; and requires at minimum the `jira_project_key` parameter.
The `label_field` parameter configures which Jira field is used to store the
labels generated from the Bugzilla status whiteboard.

`init` should return a __call__able
"""
import logging

from jbi import ActionResult, Operation
from jbi.actions.steps import (
    add_jira_comments_for_changes,
    add_link_to_bugzilla,
    add_link_to_jira,
    create_comment,
    create_issue,
    maybe_delete_duplicate,
    update_issue,
)
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


def init(jira_project_key, sync_whiteboard_labels=True):
    """Function that takes required and optional params and returns a callable object"""
    return DefaultExecutor(
        jira_project_key=jira_project_key, sync_whiteboard_labels=sync_whiteboard_labels
    )


class DefaultExecutor:
    """Callable class that encapsulates the default action."""

    def __init__(self, **parameters):
        """Initialize DefaultExecutor Object"""
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
            ],
            Operation.UPDATE: [
                update_issue,
                add_jira_comments_for_changes,
            ],
            Operation.COMMENT: [
                create_comment,
            ],
        }
        for step in steps[context.operation]:
            context, step_responses = step(context=context, **self.parameters)
            responses += step_responses

        return True, {"responses": responses}
