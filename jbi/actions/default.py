"""
Default action is listed below.
`init` is required; and requires at minimum the `jira_project_key` parameter.
The `label_field` parameter configures which Jira field is used to store the
labels generated from the Bugzilla status whiteboard.

`init` should return a __call__able
"""
import logging

from jbi import ActionResult, Operation
from jbi.environment import get_settings
from jbi.models import ActionContext
from jbi.services import bugzilla, jira

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


def create_comment(context: ActionContext, **parameters):
    """Create a Jira comment if event is `"comment"`"""
    bug = context.bug

    if bug.comment is None:
        logger.debug(
            "No matching comment found in payload",
            extra=context.dict(),
        )
        return context, ()

    jira_response = jira.add_jira_comment(context)
    return context, (jira_response,)


def create_issue(
    context: ActionContext, **parameters
):  # pylint: disable=too-many-arguments
    """Create Jira issue and establish link between bug and issue; rollback/delete if required"""
    sync_whiteboard_labels: bool = parameters["sync_whiteboard_labels"]
    bug = context.bug

    # In the payload of a bug creation, the `comment` field is `null`.
    # We fetch the list of comments to use the first one as the Jira issue description.
    comment_list = bugzilla.get_client().get_comments(bug.id)
    description = comment_list[0].text if comment_list else ""

    jira_create_response = jira.create_jira_issue(
        context,
        description,
        sync_whiteboard_labels=sync_whiteboard_labels,
    )
    issue_key = jira_create_response.get("key")

    context = context.update(jira=context.jira.update(issue=issue_key))
    return context, (jira_create_response,)


def add_link_to_jira(context: ActionContext, **parameters):
    """Add see_also field on Bugzilla ticket"""
    bugzilla_response = bugzilla.add_link_to_jira(context)
    return context, (bugzilla_response,)


def add_link_to_bugzilla(context: ActionContext, **parameters):
    """Add link Jira issue"""
    jira_response = jira.add_link_to_bugzilla(context)
    return context, (jira_response,)


def maybe_delete_duplicate(context: ActionContext, **parameters):
    """
    In the time taken to create the Jira issue the bug may have been updated so
    re-retrieve it to ensure we have the latest data.
    """
    latest_bug = bugzilla.get_client().get_bug(context.bug.id)
    jira_response_delete = jira.delete_jira_issue_if_duplicate(context, latest_bug)
    if jira_response_delete:
        return context, (jira_response_delete,)
    return context, ()


def update_issue(context: ActionContext, **parameters):
    """Update the Jira issue if bug with linked issue is modified."""
    sync_whiteboard_labels: bool = parameters["sync_whiteboard_labels"]

    resp = jira.update_jira_issue(context, sync_whiteboard_labels)

    return context, (resp,)


def add_jira_comments_for_changes(context: ActionContext, **parameters):
    """Add a Jira comment for each field change on Bugzilla"""
    comments_responses = jira.add_jira_comments_for_changes(context)

    return context, tuple(comments_responses)
