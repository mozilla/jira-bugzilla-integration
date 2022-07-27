"""
Extended action that provides some additional features over the default:
  * Updates the Jira assignee when the bug's assignee changes.
  * Optionally updates the Jira status when the bug's resolution or status changes.

`init` is required; and requires at minimum the `jira_project_key` parameter. `status_map` is optional.

`init` should return a __call__able
"""
import logging

from src.jbi import Operations
from src.jbi.bugzilla import BugzillaBug, BugzillaWebhookRequest
from src.jbi.whiteboard_actions.default import DefaultExecutor

logger = logging.getLogger(__name__)


def init(status_map=None, **kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return AssigneeAndStatusExecutor(status_map=status_map or {}, **kwargs)


class AssigneeAndStatusExecutor(DefaultExecutor):
    """Callable class that encapsulates the default_with_assignee_and_status action."""

    def __init__(self, status_map, **kwargs):
        """Initialize AssigneeAndStatusExecutor Object"""
        super().__init__(**kwargs)
        self.status_map = status_map

    def jira_comments_for_update(
        self,
        payload: BugzillaWebhookRequest,
    ):
        """Returns the comments to post to Jira for a changed bug"""
        return payload.map_as_comments(
            status_log_enabled=False, assignee_log_enabled=False
        )

    def update_issue(
        self,
        payload: BugzillaWebhookRequest,
        bug_obj: BugzillaBug,
        linked_issue_key: str,
        is_new: bool,
    ):
        changed_fields = payload.event.changed_fields() or []

        log_context = {
            "bug": {
                "id": bug_obj.id,
                "status": bug_obj.status,
                "resolution": bug_obj.resolution,
                "assigned_to": bug_obj.assigned_to,
            },
            "jira": linked_issue_key,
            "changed_fields": changed_fields,
            "operation": Operations.UPDATE,
        }

        def clear_assignee():
            # New tickets already have no assignee.
            if not is_new:
                logger.debug("Clearing assignee", extra=log_context)
                self.jira_client.update_issue_field(
                    key=linked_issue_key, fields={"assignee": None}
                )

        # If this is a new issue or if the bug's assignee has changed then
        # update the assignee.
        if is_new or "assigned_to" in changed_fields:
            if bug_obj.assigned_to == "nobody@mozilla.org":
                clear_assignee()
            else:
                logger.debug(
                    "Attempting to update assignee",
                    extra=log_context,
                )
                # Look up this user in Jira
                users = self.jira_client.user_find_by_user_string(
                    query=bug_obj.assigned_to
                )
                if len(users) == 1:
                    try:
                        # There doesn't appear to be an easy way to verify that
                        # this user can be assigned to this issue, so just try
                        # and do it.
                        self.jira_client.update_issue_field(
                            key=linked_issue_key,
                            fields={"assignee": {"accountId": users[0]["accountId"]}},
                        )
                    except IOError as exception:
                        logger.debug(
                            "Setting assignee failed: %s", exception, extra=log_context
                        )
                        # If that failed then just fall back to clearing the
                        # assignee.
                        clear_assignee()
                else:
                    logger.debug(
                        "No assignee found",
                        extra={**log_context, "operation": Operations.IGNORE},
                    )
                    clear_assignee()

        # If this is a new issue or if the bug's status or resolution has
        # changed then update the issue status.
        if is_new or "status" in changed_fields or "resolution" in changed_fields:
            # We use resolution if one exists or status otherwise.
            status = bug_obj.resolution or bug_obj.status

            if status in self.status_map:
                logger.debug(
                    "Updating Jira status to %s",
                    self.status_map[status],
                    extra=log_context,
                )
                self.jira_client.set_issue_status(
                    linked_issue_key, self.status_map[status]
                )
            else:
                logger.debug(
                    "Bug status was not in the status map.",
                    extra={
                        **log_context,
                        "status_map": self.status_map,
                        "operation": Operations.IGNORE,
                    },
                )
