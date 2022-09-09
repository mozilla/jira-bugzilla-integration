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
from jbi.actions.default import (
    maybe_create_comment,
    maybe_create_issue,
    maybe_update_issue,
)
from jbi.models import ActionContext
from jbi.services import jira

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
        context, comment_responses = maybe_create_comment(
            context=context, **self.parameters
        )
        context, create_responses = maybe_create_issue(
            context=context, **self.parameters
        )
        context, update_responses = maybe_update_issue(
            context=context, **self.parameters
        )

        context, assign_responses = maybe_assign_jira_user(
            context=context, **self.parameters
        )

        context, resolution_responses = maybe_update_issue_resolution(
            context=context, **self.parameters
        )

        context, status_responses = maybe_update_issue_status(
            context=context, **self.parameters
        )

        is_noop = context.operation == Operation.IGNORE
        if is_noop:
            logger.debug(
                "Ignore event target %r",
                context.event.target,
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


def maybe_assign_jira_user(context: ActionContext, **parameters):
    """Assign the user on the Jira issue, based on the Bugzilla assignee email"""
    if context.operation not in (Operation.CREATE, Operation.UPDATE):
        return context, ()

    event = context.event
    bug = context.bug
    linked_issue_key = context.jira.issue
    assert linked_issue_key  # Until we have more fine-grained typing of contexts

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

    # This happens when exceptions are raised an ignored.
    return context, ()


def maybe_update_issue_resolution(
    context: ActionContext,
    **parameters,
):
    """
    Update the Jira issue status
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    resolution_map: dict[str, str] = parameters["resolution_map"]
    jira_resolution = resolution_map.get(context.bug.resolution or "")
    if jira_resolution is None:
        logger.debug(
            "Bug resolution was not in the resolution map.",
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
        return context, ()

    event = context.event
    linked_issue_key = context.jira.issue
    assert linked_issue_key  # Until we have more fine-grained typing of contexts

    if context.operation == Operation.CREATE:
        if resp := jira.update_issue_resolution(
            context, linked_issue_key, jira_resolution
        ):
            return context, (resp,)

    if context.operation == Operation.UPDATE:
        changed_fields = event.changed_fields() or []

        if "resolution" in changed_fields:
            resp = jira.update_issue_resolution(
                context, linked_issue_key, jira_resolution
            )
            return context, (resp,)

    return context, ()


def maybe_update_issue_status(context: ActionContext, **parameters):
    """
    Update the Jira issue resolution
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    resolution_map: dict[str, str] = parameters["status_map"]
    bz_status = context.bug.resolution or context.bug.status
    jira_status = resolution_map.get(bz_status or "")

    event = context.event
    linked_issue_key = context.jira.issue
    assert linked_issue_key  # Until we have more fine-grained typing of contexts

    if jira_status is None:
        logger.debug(
            "Bug status was not in the status map.",
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
        return context, ()

    if context.operation == Operation.CREATE:
        resp = jira.update_issue_status(context, linked_issue_key, jira_status)
        return context, (resp,)

    if context.operation == Operation.UPDATE:
        changed_fields = event.changed_fields() or []

        if "status" in changed_fields or "resolution" in changed_fields:
            if resp := jira.update_issue_status(context, linked_issue_key, jira_status):
                return context, (resp,)

    return context, ()
