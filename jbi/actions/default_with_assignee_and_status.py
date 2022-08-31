"""
Extended action that provides some additional features over the default:
  * Updates the Jira assignee when the bug's assignee changes.
  * Optionally updates the Jira status when the bug's resolution or status changes.

`init` is required; and requires at minimum the `jira_project_key` parameter. `status_map` is optional.

`init` should return a __call__able
"""
import logging

from jbi import Operation
from jbi.actions.default import (
    JIRA_REQUIRED_PERMISSIONS as DEFAULT_JIRA_REQUIRED_PERMISSIONS,
)
from jbi.actions.default import DefaultExecutor
from jbi.models import ActionLogContext, BugzillaBug, BugzillaWebhookEvent
from jbi.services import jira

logger = logging.getLogger(__name__)


JIRA_REQUIRED_PERMISSIONS = DEFAULT_JIRA_REQUIRED_PERMISSIONS


def init(status_map=None, resolution_map=None, **kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return AssigneeAndStatusExecutor(
        status_map=status_map or {}, resolution_map=resolution_map or {}, **kwargs
    )


class AssigneeAndStatusExecutor(DefaultExecutor):
    """Callable class that encapsulates the default_with_assignee_and_status action."""

    def __init__(self, status_map, resolution_map, **kwargs):
        """Initialize AssigneeAndStatusExecutor Object"""
        super().__init__(**kwargs)
        self.status_map = status_map
        self.resolution_map = resolution_map

    def on_create_issue(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        issue_key: str,
    ):
        log_context = log_context.update(
            extra={
                **log_context.extra,
                "status_map": self.status_map,
                "resolution_map": self.resolution_map,
            },
        )

        if bug.is_assigned():
            try:
                assign_jira_user(log_context, issue_key, bug.assigned_to)
            except ValueError as exc:
                logger.debug(str(exc), extra=log_context.dict())

        jira_resolution = self.resolution_map.get(bug.resolution)
        update_issue_resolution(log_context, issue_key, jira_resolution)

        # We use resolution if one exists or status otherwise.
        bz_status = bug.resolution or bug.status
        jira_status = self.status_map.get(bz_status)
        update_issue_status(log_context, issue_key, jira_status)

    def on_update_issue(
        self,
        log_context: ActionLogContext,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        issue_key: str,
    ):
        # We don't do the upper class updates (add comments for status and assignee).
        log_context = log_context.update(
            extra={
                **log_context.extra,
                "status_map": self.status_map,
                "resolution_map": self.resolution_map,
            },
        )

        changed_fields = event.changed_fields() or []

        if "assigned_to" in changed_fields:
            if not bug.is_assigned():
                clear_assignee(log_context, issue_key)
            else:
                try:
                    assign_jira_user(log_context, issue_key, bug.assigned_to)
                except ValueError as exc:
                    logger.debug(str(exc), extra=log_context.dict())
                    # If that failed then just fall back to clearing the assignee.
                    clear_assignee(log_context, issue_key)

        if "resolution" in changed_fields:
            jira_resolution = self.resolution_map.get(bug.resolution)
            update_issue_resolution(log_context, issue_key, jira_resolution)

        if "status" in changed_fields or "resolution" in changed_fields:
            bz_status = bug.resolution or bug.status
            jira_status = self.status_map.get(bz_status)
            update_issue_status(log_context, issue_key, jira_status)


def clear_assignee(context, issue_key):
    """Clear the assignee of the specified Jira issue."""
    logger.debug("Clearing assignee", extra=context.dict())
    jira.get_client().update_issue_field(key=issue_key, fields={"assignee": None})


def find_jira_user(context, bugzilla_assignee):
    """Lookup Jira users, raise an error if not exactly one found."""
    users = jira.get_client().user_find_by_user_string(query=bugzilla_assignee)
    if len(users) != 1:
        raise ValueError(f"User {bugzilla_assignee} not found")
    return users[0]


def assign_jira_user(context, issue_key, bugzilla_assignee):
    """Set the assignee of the specified Jira issue, raise if fails."""
    jira_user = find_jira_user(context, bugzilla_assignee)
    jira_user_id = jira_user["accountId"]
    try:
        # There doesn't appear to be an easy way to verify that
        # this user can be assigned to this issue, so just try
        # and do it.
        return jira.get_client().update_issue_field(
            key=issue_key,
            fields={"assignee": {"accountId": jira_user_id}},
        )
    except IOError as exc:
        raise ValueError(
            f"Could not assign {jira_user_id} to issue {issue_key}"
        ) from exc


def update_issue_status(context, issue_key, jira_status):
    """Update the status of the Jira issue or no-op if None."""
    if jira_status:
        logger.debug(
            "Updating Jira status to %s",
            jira_status,
            extra=context.dict(),
        )
        jira.get_client().set_issue_status(
            issue_key,
            jira_status,
        )
    else:
        logger.debug(
            "Bug status was not in the status map.",
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )


def update_issue_resolution(context, issue_key, jira_resolution):
    """Update the resolution of the Jira issue or no-op if None."""
    if jira_resolution:
        logger.debug(
            "Updating Jira resolution to %s",
            jira_resolution,
            extra=context.dict(),
        )
        jira.get_client().update_issue_field(
            key=issue_key,
            fields={"resolution": jira_resolution},
        )
    else:
        logger.debug(
            "Bug resolution was not in the resolution map.",
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
