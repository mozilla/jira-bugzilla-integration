"""
Default action is listed below.
`init` is required; and requires at minimum the `jira_project_key` parameter.
The `label_field` parameter configures which Jira field is used to store the
labels generated from the Bugzilla status whiteboard.

`init` should return a __call__able
"""
import json
import logging
from typing import Any

from jbi import ActionResult, Operation
from jbi.environment import get_settings
from jbi.errors import ActionError
from jbi.models import (
    ActionLogContext,
    BugzillaBug,
    BugzillaWebhookComment,
    BugzillaWebhookEvent,
    JiraContext,
)
from jbi.services import bugzilla, jira

settings = get_settings()

logger = logging.getLogger(__name__)

JIRA_DESCRIPTION_CHAR_LIMIT = 32767
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

    def __call__(  # pylint: disable=inconsistent-return-statements
        self,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
    ) -> ActionResult:
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

        if event.target == "comment":
            log_context = log_context.update(operation=Operation.COMMENT)
            if linked_issue_key:
                return self.comment_create_or_noop(
                    log_context=log_context,
                    bug=bug,
                    event=event,
                    linked_issue_key=linked_issue_key,
                )

        if event.target == "bug":
            if not linked_issue_key:
                # Create
                log_context = log_context.update(operation=Operation.CREATE)
                return self.create_issue(log_context=log_context, bug=bug, event=event)

            # Update
            changed_fields = event.changed_fields() or []
            log_context = log_context.update(
                operation=Operation.UPDATE,
                extra={
                    "changed_fields": ", ".join(changed_fields),
                },
            )
            return self.update_issue(
                log_context=log_context,
                bug=bug,
                event=event,
                linked_issue_key=linked_issue_key,
            )

        logger.debug(
            "Ignore event target %r",
            event.target,
            extra=log_context.dict(),
        )
        return False, {}

    def comment_create_or_noop(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        linked_issue_key: str,
    ) -> ActionResult:
        """Confirm issue is already linked, then apply comments; otherwise noop"""
        if bug.comment is None:
            logger.debug(
                "No matching comment found in payload",
                extra=log_context.dict(),
            )
            return False, {}

        commenter = event.user.login if event.user else "unknown"
        jira_response = add_jira_comment(
            log_context, linked_issue_key, commenter, bug.comment
        )
        return True, {"jira_response": jira_response}

    def create_issue(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
    ) -> ActionResult:
        """create jira issue and establish link between bug and issue; rollback/delete if required"""
        issue_key = create_jira_issue(
            log_context,
            bug,
            self.jira_project_key,
            sync_whiteboard_labels=self.sync_whiteboard_labels,
        )

        log_context.jira.issue = issue_key

        bug = bugzilla.get_client().get_bug(bug.id)
        jira_response_delete = delete_jira_issue_if_duplicate(
            log_context, bug, issue_key
        )
        if jira_response_delete:
            return True, {"jira_response": jira_response_delete}

        bugzilla_response = add_link_to_jira(log_context, bug, issue_key)

        jira_response = add_link_to_bugzilla(log_context, issue_key, bug)

        self.on_create_issue(log_context, bug=bug, event=event, issue_key=issue_key)

        return True, {
            "bugzilla_response": bugzilla_response,
            "jira_response": jira_response,
        }

    def on_create_issue(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        issue_key: str,
    ):
        """Allows sub-classes to act when a Jira issue is created"""

    def update_issue(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        linked_issue_key: str,
    ) -> ActionResult:
        """Update existing an Jira issue."""
        jira_response_update = update_jira_issue(
            log_context, bug, linked_issue_key, self.sync_whiteboard_labels
        )

        extra_updates = self.on_update_issue(
            log_context=log_context,
            bug=bug,
            event=event,
            issue_key=linked_issue_key,
        )

        return True, {"jira_responses": [jira_response_update, extra_updates]}

    def on_update_issue(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        issue_key: str,
    ):
        """Allows sub-classes to act when a Jira issue is updated,
        by default, add comments for changes"""
        jira_response_comments = []
        if event.changes:
            jira_response_comments = add_jira_comments_for_changes(
                log_context, event, bug, issue_key
            )
        return jira_response_comments


def create_jira_issue(context, bug, jira_project_key, sync_whiteboard_labels) -> str:
    """Create a Jira issue with the specified fields and return its key."""
    logger.debug(
        "Create new Jira issue for Bug %s",
        bug.id,
        extra=context.dict(),
    )
    comment_list = bugzilla.get_client().get_comments(bug.id)
    description = comment_list[0].text[:JIRA_DESCRIPTION_CHAR_LIMIT]
    fields: dict[str, Any] = {
        "summary": bug.summary,
        "issuetype": {"name": bug.issue_type()},
        "description": description,
        "project": {"key": jira_project_key},
    }
    if sync_whiteboard_labels:
        fields["labels"] = bug.get_jira_labels()

    jira_response_create = jira.get_client().create_issue(fields=fields)

    # Jira response can be of the form: List or Dictionary
    if isinstance(jira_response_create, list):
        # if a list is returned, get the first item
        jira_response_create = jira_response_create[0]

    if isinstance(jira_response_create, dict):
        # if a dict is returned or the first item in a list, confirm there are no errors
        if any(
            element in ["errors", "errorMessages"] and jira_response_create[element]
            for element in jira_response_create.keys()
        ):
            raise ActionError(f"response contains error: {jira_response_create}")

    issue_key: str = jira_response_create.get("key")
    return issue_key


def update_jira_issue(context, bug, issue_key, sync_whiteboard_labels):
    """Update the fields of an existing Jira issue"""
    logger.debug(
        "Update fields of Jira issue %s for Bug %s",
        issue_key,
        bug.id,
        extra=context.dict(),
    )
    fields = {
        "summary": bug.summary,
    }
    if sync_whiteboard_labels:
        fields["labels"] = bug.get_jira_labels()

    jira_response_update = jira.get_client().update_issue_field(
        key=issue_key, fields=fields
    )
    return jira_response_update


def add_jira_comment(
    context, issue_key, commenter: str, comment: BugzillaWebhookComment
):
    """Publish a comment on the specified Jira issue"""
    formatted_comment = f"*({commenter})* commented: \n{{quote}}{comment.body}{{quote}}"
    jira_response = jira.get_client().issue_add_comment(
        issue_key=issue_key,
        comment=formatted_comment,
    )
    logger.debug(
        "User comment added to Jira issue %s",
        issue_key,
        extra=context.dict(),
    )
    return jira_response


def add_jira_comments_for_changes(log_context, event, bug, linked_issue_key):
    """Add comments on the specified Jira issue for each change of the event"""
    comments: list = []
    user = event.user.login if event.user else "unknown"
    for change in event.changes:
        if change.field in ["status", "resolution"]:
            comments.append(
                {
                    "modified by": user,
                    "resolution": bug.resolution,
                    "status": bug.status,
                }
            )
        if change.field in ["assigned_to", "assignee"]:
            comments.append({"assignee": bug.assigned_to})

    jira_response_comments = []
    for i, comment in enumerate(comments):
        logger.debug(
            "Create comment #%s on Jira issue %s",
            i + 1,
            linked_issue_key,
            extra=log_context.update(operation=Operation.COMMENT).dict(),
        )
        jira_response = jira.get_client().issue_add_comment(
            issue_key=linked_issue_key, comment=json.dumps(comment, indent=4)
        )
        jira_response_comments.append(jira_response)

    return jira_response_comments


def delete_jira_issue_if_duplicate(context, bug, issue_key):
    """Rollback the Jira issue creation if there is already a linked Jira issue
    on the Bugzilla ticket"""
    # In the time taken to create the Jira issue the bug may have been updated so
    # re-retrieve it to ensure we have the latest data.
    jira_key_in_bugzilla = bug.extract_from_see_also()
    _duplicate_creation_event = (
        jira_key_in_bugzilla is not None and issue_key != jira_key_in_bugzilla
    )
    if not _duplicate_creation_event:
        return None

    logger.warning(
        "Delete duplicated Jira issue %s from Bug %s",
        issue_key,
        bug.id,
        extra=context.update(operation=Operation.DELETE).dict(),
    )
    jira_response_delete = jira.get_client().delete_issue(issue_id_or_key=issue_key)
    return jira_response_delete


def add_link_to_jira(context, bug, issue_key):
    """Add link to Jira in Bugzilla ticket"""
    jira_url = f"{settings.jira_base_url}browse/{issue_key}"
    logger.debug(
        "Link %r on Bug %s",
        jira_url,
        bug.id,
        extra=context.update(operation=Operation.LINK).dict(),
    )
    return bugzilla.get_client().update_bug(bug.id, see_also_add=jira_url)


def add_link_to_bugzilla(context, issue_key, bug):
    """Add link to Bugzilla ticket in Jira issue"""
    bugzilla_url = f"{settings.bugzilla_base_url}/show_bug.cgi?id={bug.id}"
    logger.debug(
        "Link %r on Jira issue %s",
        bugzilla_url,
        issue_key,
        extra=context.update(operation=Operation.LINK).dict(),
    )
    icon_url = f"{settings.bugzilla_base_url}/favicon.ico"
    return jira.get_client().create_or_update_issue_remote_links(
        issue_key=issue_key,
        link_url=bugzilla_url,
        title=bugzilla_url,
        icon_url=icon_url,
        icon_title=icon_url,
    )
