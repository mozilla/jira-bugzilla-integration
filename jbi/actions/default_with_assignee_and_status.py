"""
Extended action that provides some additional features over the default:
  * Updates the Jira assignee when the bug's assignee changes.
  * Optionally updates the Jira status when the bug's resolution or status changes.

`init` is required; and requires at minimum the `jira_project_key` parameter. `status_map` is optional.

`init` should return a __call__able
"""
import logging
from typing import Optional

from jbi import ActionResult, Operation
from jbi.actions.default import (
    JIRA_REQUIRED_PERMISSIONS as DEFAULT_JIRA_REQUIRED_PERMISSIONS,
)
from jbi.actions.default import (
    maybe_create_comment,
    maybe_create_issue,
    maybe_update_issue,
)
from jbi.models import ActionContext, BugzillaBug, BugzillaWebhookEvent, JiraContext
from jbi.services import jira

logger = logging.getLogger(__name__)


JIRA_REQUIRED_PERMISSIONS = DEFAULT_JIRA_REQUIRED_PERMISSIONS


def init(
    jira_project_key,
    sync_whiteboard_labels=True,
    status_map=None,
    resolution_map=None,
    **kwargs,
):
    """Function that takes required and optional params and returns a callable object"""
    return AssigneeAndStatusExecutor(
        jira_project_key,
        sync_whiteboard_labels,
        status_map=status_map or {},
        resolution_map=resolution_map or {},
        **kwargs,
    )


class AssigneeAndStatusExecutor:
    """Callable class that encapsulates the default_with_assignee_and_status action."""

    def __init__(
        self,
        jira_project_key,
        sync_whiteboard_labels,
        status_map,
        resolution_map,
        **kwargs,
    ):
        """Initialize AssigneeAndStatusExecutor Object"""
        self.jira_project_key = jira_project_key
        self.sync_whiteboard_labels = sync_whiteboard_labels
        self.status_map = status_map
        self.resolution_map = resolution_map

    def __call__(  # pylint: disable=too-many-branches,duplicate-code
        self,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
    ) -> ActionResult:
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""
        linked_issue_key = bug.extract_from_see_also()

        context = ActionContext(
            event=event,
            bug=bug,
            operation=Operation.IGNORE,
            jira=JiraContext(
                issue=linked_issue_key,
                project=self.jira_project_key,
            ),
            extra={
                "status_map": str(self.status_map),
                "resolution_map": str(self.resolution_map),
            },
        )

        context, comment_responses = maybe_create_comment(context=context)
        context, create_responses = maybe_create_issue(
            sync_whiteboard_labels=self.sync_whiteboard_labels,
            context=context,
        )
        context, update_responses = maybe_update_issue(
            context=context, sync_whiteboard_labels=self.sync_whiteboard_labels
        )

        context, assign_responses = maybe_assign_jira_user(context=context)

        jira_resolution = self.resolution_map.get(bug.resolution)
        context, resolution_responses = maybe_update_issue_resolution(
            context=context, jira_resolution=jira_resolution
        )

        bz_status = bug.resolution or bug.status
        jira_status = self.status_map.get(bz_status)
        context, status_responses = maybe_update_issue_status(
            context=context, jira_status=jira_status
        )

        is_noop = context.operation == Operation.IGNORE
        if is_noop:
            logger.debug(
                "Ignore event target %r",
                event.target,
                extra=context.dict(),
            )

        return not is_noop, {
            "responses": comment_responses
            + create_responses
            + update_responses
            + assign_responses
            + resolution_responses
            + status_responses
        }


def maybe_assign_jira_user(context: ActionContext):
    event = context.event
    bug = context.bug
    linked_issue_key = context.jira.issue

    if context.operation == Operation.CREATE:
        if not bug.is_assigned():
            return context, ()

        try:
            resp = jira.assign_jira_user(
                context, linked_issue_key, bug.assigned_to  # type: ignore
            )
            return context, (resp,)
        except ValueError as exc:
            logger.debug(str(exc), extra=context.dict())

    if context.operation == Operation.UPDATE:
        changed_fields = event.changed_fields() or []

        if "assigned_to" not in changed_fields:
            return context, ()

        if not bug.is_assigned():
            resp = jira.clear_assignee(context, linked_issue_key)
        else:
            try:
                resp = jira.assign_jira_user(
                    context, linked_issue_key, bug.assigned_to  # type: ignore
                )
            except ValueError as exc:
                logger.debug(str(exc), extra=context.dict())
                # If that failed then just fall back to clearing the assignee.
                resp = jira.clear_assignee(context, linked_issue_key)
        return context, (resp,)

    return context, ()


def maybe_update_issue_resolution(
    context: ActionContext,
    jira_resolution: Optional[str],
):
    event = context.event
    linked_issue_key = context.jira.issue

    if context.operation == Operation.CREATE:
        if resp := jira.maybe_update_issue_resolution(
            context, linked_issue_key, jira_resolution
        ):
            return context, (resp,)

    if context.operation == Operation.UPDATE:
        changed_fields = event.changed_fields() or []

        if "resolution" in changed_fields:
            if resp := jira.maybe_update_issue_resolution(
                context, linked_issue_key, jira_resolution
            ):
                return context, (resp,)

    return context, ()


def maybe_update_issue_status(
    context: ActionContext,
    jira_status: Optional[str],
):
    event = context.event
    linked_issue_key = context.jira.issue

    if context.operation == Operation.CREATE:
        if resp := jira.maybe_update_issue_status(
            context, linked_issue_key, jira_status
        ):
            return context, (resp,)

    if context.operation == Operation.UPDATE:
        changed_fields = event.changed_fields() or []

        if "status" in changed_fields or "resolution" in changed_fields:
            if resp := jira.maybe_update_issue_status(
                context, linked_issue_key, jira_status
            ):
                return context, (resp,)

    return context, ()
