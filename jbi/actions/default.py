"""
Default action is listed below.
`init` is required; and requires at minimum the `jira_project_key` parameter.
The `label_field` parameter configures which Jira field is used to store the
labels generated from the Bugzilla status whiteboard.

`init` should return a __call__able
"""
import logging
from typing import Optional

from jbi import ActionResult, Operation
from jbi.environment import get_settings
from jbi.models import ActionLogContext, BugzillaBug, BugzillaWebhookEvent, JiraContext
from jbi.services import bugzilla, jira

settings = get_settings()

logger = logging.getLogger(__name__)

JIRA_REQUIRED_PERMISSIONS = {
    "ADD_COMMENTS",
    "CREATE_ISSUES",
    "DELETE_ISSUES",
    "EDIT_ISSUES",
}


def init(jira_project_key, sync_whiteboard_labels=True, **kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return DefaultExecutor(
        jira_project_key=jira_project_key,
        sync_whiteboard_labels=sync_whiteboard_labels,
        **kwargs,
    )


class DefaultExecutor:
    """Callable class that encapsulates the default action."""

    def __init__(self, jira_project_key, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.jira_project_key = jira_project_key
        self.sync_whiteboard_labels = kwargs.get("sync_whiteboard_labels", True)

    def __call__(self, bug: BugzillaBug, event: BugzillaWebhookEvent) -> ActionResult:
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""
        linked_issue_key = bug.extract_from_see_also()

        log_context = ActionLogContext(
            event=event,
            bug=bug,
            operation=Operation.IGNORE,
            jira=JiraContext(
                issue=linked_issue_key,
                project=self.jira_project_key,
            ),
        )

        operation_kwargs = dict(
            log_context=log_context,
            bug=bug,
            event=event,
            linked_issue_key=linked_issue_key,
        )

        if result := maybe_create_comment(**operation_kwargs):
            _, responses = result
            return True, {"responses": responses}

        if result := maybe_create_issue(
            jira_project_key=self.jira_project_key,
            sync_whiteboard_labels=self.sync_whiteboard_labels,
            **operation_kwargs,
        ):
            _, responses = result
            return True, {"responses": responses}

        if result := maybe_update_issue(
            **operation_kwargs, sync_whiteboard_labels=self.sync_whiteboard_labels
        ):
            context, responses = result
            comments_responses = jira.add_jira_comments_for_changes(
                **{**operation_kwargs, "log_context": context}
            )
            return True, {"responses": responses + comments_responses}

        logger.debug(
            "Ignore event target %r",
            event.target,
            extra=log_context.dict(),
        )
        return False, {}


def maybe_create_comment(
    log_context: ActionLogContext,
    bug: BugzillaBug,
    event: BugzillaWebhookEvent,
    linked_issue_key: Optional[str],
):
    """Create a Jira comment if event is `"comment"`"""
    if event.target != "comment" or not linked_issue_key:
        return None

    if bug.comment is None:
        logger.debug(
            "No matching comment found in payload",
            extra=log_context.dict(),
        )
        return None

    log_context = log_context.update(operation=Operation.COMMENT)
    commenter = event.user.login if event.user else "unknown"
    jira_response = jira.add_jira_comment(
        log_context, linked_issue_key, commenter, bug.comment
    )
    return log_context, [jira_response]


def maybe_create_issue(
    log_context: ActionLogContext,
    bug: BugzillaBug,
    event: BugzillaWebhookEvent,
    linked_issue_key: Optional[str],
    jira_project_key: str,
    sync_whiteboard_labels: bool,
):  # pylint: disable=too-many-arguments
    """Create Jira issue and establish link between bug and issue; rollback/delete if required"""
    if event.target != "bug" or linked_issue_key:
        return None

    log_context = log_context.update(operation=Operation.CREATE)

    # In the payload of a bug creation, the `comment` field is `null`.
    # We fetch the list of comments to use the first one as the Jira issue description.
    comment_list = bugzilla.get_client().get_comments(bug.id)
    description = comment_list[0].text if comment_list else ""

    issue_key = jira.create_jira_issue(
        log_context,
        bug,
        description,
        jira_project_key,
        sync_whiteboard_labels=sync_whiteboard_labels,
    )

    log_context.jira.issue = issue_key

    bug = bugzilla.get_client().get_bug(bug.id)
    jira_response_delete = jira.delete_jira_issue_if_duplicate(
        log_context, bug, issue_key
    )
    if jira_response_delete:
        return jira_response_delete

    bugzilla_response = bugzilla.add_link_to_jira(log_context, bug, issue_key)

    jira_response = jira.add_link_to_bugzilla(log_context, issue_key, bug)

    return log_context, [bugzilla_response, jira_response]


def maybe_update_issue(
    log_context: ActionLogContext,
    bug: BugzillaBug,
    event: BugzillaWebhookEvent,
    linked_issue_key: Optional[str],
    sync_whiteboard_labels: bool,
):
    """Update the Jira issue if bug with linked issue is modified."""
    if event.target != "bug" or not linked_issue_key:
        return None

    changed_fields = event.changed_fields() or []
    log_context = log_context.update(
        operation=Operation.UPDATE,
        extra={
            "changed_fields": ", ".join(changed_fields),
        },
    )
    jira_response_update = jira.update_jira_issue(
        log_context, bug, linked_issue_key, sync_whiteboard_labels
    )

    return log_context, [jira_response_update]
