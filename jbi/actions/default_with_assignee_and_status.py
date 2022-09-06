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
from jbi.models import ActionLogContext, BugzillaBug, BugzillaWebhookEvent, JiraContext
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

        log_context = ActionLogContext(
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

        operation_kwargs = dict(
            log_context=log_context,
            bug=bug,
            event=event,
            linked_issue_key=linked_issue_key,
        )

        if result := maybe_create_comment(**operation_kwargs):
            _, responses = result
            return True, responses

        if result := maybe_create_issue(
            jira_project_key=self.jira_project_key,
            sync_whiteboard_labels=self.sync_whiteboard_labels,
            **operation_kwargs,
        ):
            log_context, responses = result

            linked_issue_key = log_context.jira.issue  # not great

            if bug.is_assigned():
                try:
                    resp = jira.assign_jira_user(
                        log_context, linked_issue_key, bug.assigned_to
                    )
                    responses.append(resp)
                except ValueError as exc:
                    logger.debug(str(exc), extra=log_context.dict())

            jira_resolution = self.resolution_map.get(bug.resolution)
            if resp := jira.maybe_update_issue_resolution(
                log_context, linked_issue_key, jira_resolution
            ):
                responses.append(resp)

            # We use resolution if one exists or status otherwise.
            bz_status = bug.resolution or bug.status
            jira_status = self.status_map.get(bz_status)
            if resp := jira.maybe_update_issue_status(
                log_context, linked_issue_key, jira_status
            ):
                responses.append(resp)
            return True, {"responses": responses}

        if result := maybe_update_issue(
            **operation_kwargs, sync_whiteboard_labels=self.sync_whiteboard_labels
        ):
            log_context, responses = result

            changed_fields = event.changed_fields() or []

            if "assigned_to" in changed_fields:
                if not bug.is_assigned():
                    resp = jira.clear_assignee(log_context, linked_issue_key)
                else:
                    try:
                        resp = jira.assign_jira_user(
                            log_context, linked_issue_key, bug.assigned_to
                        )
                    except ValueError as exc:
                        logger.debug(str(exc), extra=log_context.dict())
                        # If that failed then just fall back to clearing the assignee.
                        resp = jira.clear_assignee(log_context, linked_issue_key)
                responses.append(resp)

            if "resolution" in changed_fields:
                jira_resolution = self.resolution_map.get(bug.resolution)
                if resp := jira.maybe_update_issue_resolution(
                    log_context, linked_issue_key, jira_resolution
                ):
                    responses.append(resp)

            if "status" in changed_fields or "resolution" in changed_fields:
                bz_status = bug.resolution or bug.status
                jira_status = self.status_map.get(bz_status)
                if resp := jira.maybe_update_issue_status(
                    log_context, linked_issue_key, jira_status
                ):
                    responses.append(resp)

            return True, {"responses": responses}

        logger.debug(
            "Ignore event target %r",
            event.target,
            extra=log_context.dict(),
        )
        return False, {}
