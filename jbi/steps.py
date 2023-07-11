"""
Collection of reusable action steps.

Each step takes an `ActionContext` and a list of arbitrary parameters.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from requests import exceptions as requests_exceptions

from jbi import Operation
from jbi.errors import IncompleteStepError

if TYPE_CHECKING:
    from jbi.models import ActionContext, ActionParams
    from jbi.services.bugzilla import BugzillaService
    from jbi.services.jira import JiraService

logger = logging.getLogger(__name__)


def create_comment(context: ActionContext, *, jira_service: JiraService):
    """Create a Jira comment using `context.bug.comment`"""
    bug = context.bug

    if bug.comment is None:
        logger.debug(
            "No matching comment found in payload",
            extra=context.dict(),
        )
        return context

    jira_response = jira_service.add_jira_comment(context)
    context = context.append_responses(jira_response)
    return context


def create_issue(
    context: ActionContext,
    *,
    parameters: ActionParams,
    jira_service: JiraService,
    bugzilla_service: BugzillaService,
):
    """Create the Jira issue with the first comment as the description."""
    bug = context.bug
    issue_type = parameters.issue_type_map.get(bug.type or "", "Task")
    # In the payload of a bug creation, the `comment` field is `null`.
    description = bugzilla_service.get_description(bug.id)

    jira_create_response = jira_service.create_jira_issue(
        context, description, issue_type
    )
    issue_key = jira_create_response.get("key")

    context = context.update(
        jira=context.jira.update(issue=issue_key),
    )
    context = context.append_responses(jira_create_response)
    return context


def add_link_to_jira(context: ActionContext, *, bugzilla_service: BugzillaService):
    """Add the URL to the Jira issue in the `see_also` field on the Bugzilla ticket"""
    bugzilla_response = bugzilla_service.add_link_to_jira(context)
    context = context.append_responses(bugzilla_response)
    return context


def add_link_to_bugzilla(context: ActionContext, *, jira_service: JiraService):
    """Add the URL of the Bugzilla ticket to the links of the Jira issue"""
    jira_response = jira_service.add_link_to_bugzilla(context)
    context = context.append_responses(jira_response)
    return context


def maybe_delete_duplicate(
    context: ActionContext,
    *,
    bugzilla_service: BugzillaService,
    jira_service: JiraService,
):
    """
    In the time taken to create the Jira issue the bug may have been updated so
    re-retrieve it to ensure we have the latest data, and delete any duplicate
    if two Jira issues were created for the same Bugzilla ticket.
    """
    latest_bug = bugzilla_service.refresh_bug_data(context.bug)
    jira_response_delete = jira_service.delete_jira_issue_if_duplicate(
        context, latest_bug
    )
    if jira_response_delete:
        context = context.append_responses(jira_response_delete)
    return context


def update_issue_summary(context: ActionContext, *, jira_service: JiraService):
    """Update the Jira issue's summary if the linked bug is modified."""

    if "summary" not in context.event.changed_fields():
        return context

    jira_response_update = jira_service.update_issue_summary(context)
    context = context.append_responses(jira_response_update)
    return context


def add_jira_comments_for_changes(context: ActionContext, *, jira_service: JiraService):
    """Add a Jira comment for each field (assignee, status, resolution) change on
    the Bugzilla ticket."""
    comments_responses = jira_service.add_jira_comments_for_changes(context)
    context.append_responses(comments_responses)
    return context


def maybe_assign_jira_user(context: ActionContext, *, jira_service: JiraService):
    """Assign the user on the Jira issue, based on the Bugzilla assignee email.

    It will attempt to assign the Jira issue the same person as the bug is assigned to. This relies on
    the user using the same email address in both Bugzilla and Jira. If the user does not exist in Jira
    then the assignee is cleared from the Jira issue. The Jira account that JBI uses requires the "Browse
    users and groups" global permission in order to set the assignee.
    """
    event = context.event
    bug = context.bug

    if context.operation == Operation.CREATE:
        if not bug.is_assigned():
            return context

        try:
            resp = jira_service.assign_jira_user(context, bug.assigned_to)  # type: ignore
            context.append_responses(resp)
            return context
        except ValueError as exc:
            logger.debug(str(exc), extra=context.dict())
            raise IncompleteStepError(context) from exc

    if context.operation == Operation.UPDATE:
        if "assigned_to" not in event.changed_fields():
            return context

        if not bug.is_assigned():
            resp = jira_service.clear_assignee(context)
        else:
            try:
                resp = jira_service.assign_jira_user(context, bug.assigned_to)  # type: ignore
            except ValueError as exc:
                logger.debug(str(exc), extra=context.dict())
                # If that failed then just fall back to clearing the assignee.
                resp = jira_service.clear_assignee(context)
        context.append_responses(resp)
        return context

    # This happens when exceptions are raised an ignored.
    return context


def maybe_update_issue_resolution(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
):
    """
    Update the Jira issue status
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """

    bz_resolution = context.bug.resolution or ""
    jira_resolution = parameters.resolution_map.get(bz_resolution)

    if jira_resolution is None:
        logger.debug(
            "Bug resolution %r was not in the resolution map.",
            bz_resolution,
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
        raise IncompleteStepError(context)

    if context.operation == Operation.CREATE:
        resp = jira_service.update_issue_resolution(context, jira_resolution)
        context.append_responses(resp)
        return context

    if context.operation == Operation.UPDATE:
        if "resolution" in context.event.changed_fields():
            resp = jira_service.update_issue_resolution(context, jira_resolution)
            context.append_responses(resp)
            return context

    return context


def maybe_update_issue_status(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
):
    """
    Update the Jira issue resolution
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    bz_status = context.bug.resolution or context.bug.status
    jira_status = parameters.status_map.get(bz_status or "")

    if jira_status is None:
        logger.debug(
            "Bug status %r was not in the status map.",
            bz_status,
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
        raise IncompleteStepError(context)

    if context.operation == Operation.CREATE:
        resp = jira_service.update_issue_status(context, jira_status)
        context.append_responses(resp)
        return context

    if context.operation == Operation.UPDATE:
        changed_fields = context.event.changed_fields()

        if "status" in changed_fields or "resolution" in changed_fields:
            resp = jira_service.update_issue_status(context, jira_status)
            context.append_responses(resp)
            return context

    return context


def maybe_update_components(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
):
    """
    Update the Jira issue components
    """
    candidate_components = set(parameters.jira_components.set_custom_components)
    if context.bug.component and parameters.jira_components.use_bug_component:
        candidate_components.add(context.bug.component)
    if context.bug.product and parameters.jira_components.use_bug_product:
        candidate_components.add(context.bug.product)
    if (
        context.bug.product_component
        and parameters.jira_components.use_bug_component_with_product_prefix
    ):
        candidate_components.add(context.bug.product_component)

    if not candidate_components:
        # no components to update
        return context

    # Although we previously introspected the project components, we
    # still have to catch any potential 400 error response here, because
    # the `components` field may not be on the create / update issue.

    if not context.jira.issue:
        raise ValueError("Jira issue unset in Action Context")

    try:
        resp, missing_components = jira_service.update_issue_components(
            issue_key=context.jira.issue,
            project=parameters.jira_project_key,
            components=candidate_components,
        )
    except requests_exceptions.HTTPError as exc:
        if exc.response.status_code != 400:
            raise
        # If `components` is not a valid field on create/update screens,
        # then warn developers and ignore the error.
        logger.error(
            f"Could not set components on issue {context.jira.issue}: %s",
            str(exc),
            extra=context.dict(),
        )
        context.append_responses(exc.response)
        raise IncompleteStepError(context) from exc

    if missing_components:
        logger.warning(
            "Could not find components '%s' in project",
            ",".join(sorted(missing_components)),
            extra=context.dict(),
        )
        raise IncompleteStepError(context)

    context.append_responses(resp)
    return context


def _whiteboard_as_labels(labels_brackets: str, whiteboard: Optional[str]) -> list[str]:
    """Split the whiteboard string into a list of labels"""
    splitted = whiteboard.replace("[", "").split("]") if whiteboard else []
    stripped = [x.strip() for x in splitted if x not in ["", " "]]
    # Jira labels can't contain a " ", convert to "."
    nospace = [wb.replace(" ", ".") for wb in stripped]
    with_brackets = [f"[{wb}]" for wb in nospace]

    if labels_brackets == "yes":
        labels = with_brackets
    elif labels_brackets == "both":
        labels = nospace + with_brackets
    else:
        labels = nospace

    return ["bugzilla"] + labels


def _build_labels_update(
    labels_brackets, added, removed=None
) -> tuple[list[str], list[str]]:
    # We don't bother detecting if label was already there.
    additions = _whiteboard_as_labels(labels_brackets, added)
    removals = []
    if removed:
        before = _whiteboard_as_labels(labels_brackets, removed)
        removals = sorted(
            set(before).difference(set(additions))
        )  # sorted for unit testing
    return additions, removals


def sync_whiteboard_labels(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
):
    """
    Set whiteboard tags as labels on the Jira issue.
    """
    # On update of whiteboard field, add/remove corresponding labels
    if context.event.changes:
        changes_by_field = {change.field: change for change in context.event.changes}
        if change := changes_by_field.get("whiteboard"):
            additions, removals = _build_labels_update(
                added=change.added,
                removed=change.removed,
                labels_brackets=parameters.labels_brackets,
            )
        else:
            # Whiteboard field not changed, ignore.
            return context
    else:
        # On creation, just add them all.
        additions, removals = _build_labels_update(
            added=context.bug.whiteboard, labels_brackets=parameters.labels_brackets
        )

    if not context.jira.issue:
        raise ValueError("Jira issue unset in Action Context")

    try:
        resp = jira_service.update_issue_labels(
            issue_key=context.jira.issue, add=additions, remove=removals
        )
    except requests_exceptions.HTTPError as exc:
        if exc.response.status_code != 400:
            raise
        # If `labels` is not a valid field in this project, then warn developers
        # and ignore the error.
        logger.error(
            f"Could not set labels on issue {context.jira.issue}: %s",
            str(exc),
            extra=context.dict(),
        )
        context.append_responses(exc.response)
        raise IncompleteStepError(context) from exc

    context.append_responses(resp)
    return context
