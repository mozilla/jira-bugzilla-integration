"""
Collection of reusable action steps.

Each step takes an `ActionContext` and a list of arbitrary parameters.
"""

# This import is needed (as of Python 3.11) to enable type checking with modules
# imported under `TYPE_CHECKING`
# https://docs.python.org/3/whatsnew/3.7.html#pep-563-postponed-evaluation-of-annotations
# https://docs.python.org/3/whatsnew/3.11.html#pep-563-may-not-be-the-future
from __future__ import annotations

import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, Iterable, Optional

from requests import exceptions as requests_exceptions

from jbi import Operation
from jbi.environment import get_settings


class StepStatus(Enum):
    """
    Options for the result of executing a step function:
        SUCCESS: The step succeeded at doing meaningful work
        INCOMPLETE: The step did not execute successfully, but it's an error we anticipated
        NOOP: The step executed successfully, but didn't have any meaningful work to do
    """

    SUCCESS = auto()
    INCOMPLETE = auto()
    NOOP = auto()


# https://docs.python.org/3.11/library/typing.html#typing.TYPE_CHECKING
if TYPE_CHECKING:
    from jbi.bugzilla.service import BugzillaService
    from jbi.jira import JiraService
    from jbi.models import ActionContext, ActionParams

    StepResult = tuple[StepStatus, ActionContext]

logger = logging.getLogger(__name__)


def create_comment(context: ActionContext, *, jira_service: JiraService) -> StepResult:
    """Create a Jira comment using `context.bug.comment`"""
    bug = context.bug

    if context.event.target == "comment":
        if bug.comment is None:
            logger.info(
                "No matching comment found in payload",
                extra=context.model_dump(),
            )
            return (StepStatus.NOOP, context)

        if not bug.comment.body:
            logger.info(
                "Comment message is empty",
                extra=context.model_dump(),
            )
            return (StepStatus.NOOP, context)

    jira_response = jira_service.add_jira_comment(context)
    context = context.append_responses(jira_response)
    return (StepStatus.SUCCESS, context)


def create_issue(
    context: ActionContext,
    *,
    parameters: ActionParams,
    jira_service: JiraService,
    bugzilla_service: BugzillaService,
) -> StepResult:
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
    return (StepStatus.SUCCESS, context)


def add_link_to_jira(
    context: ActionContext, *, bugzilla_service: BugzillaService
) -> StepResult:
    """Add the URL to the Jira issue in the `see_also` field on the Bugzilla ticket"""
    settings = get_settings()
    jira_url = f"{settings.jira_base_url}browse/{context.jira.issue}"
    logger.info(
        "Link %r on Bug %s",
        jira_url,
        context.bug.id,
        extra=context.update(operation=Operation.LINK).model_dump(),
    )
    bugzilla_response = bugzilla_service.add_link_to_see_also(context.bug, jira_url)
    context = context.append_responses(bugzilla_response)
    return (StepStatus.SUCCESS, context)


def add_link_to_bugzilla(
    context: ActionContext, *, jira_service: JiraService
) -> StepResult:
    """Add the URL of the Bugzilla ticket to the links of the Jira issue"""
    jira_response = jira_service.add_link_to_bugzilla(context)
    context = context.append_responses(jira_response)
    return (StepStatus.SUCCESS, context)


def maybe_add_phabricator_link(
    context: ActionContext,
    *,
    jira_service: JiraService,
) -> StepResult:
    """Add a phabricator link to the Jira issue if an attachment is a phabricator attachment"""
    if context.event.target != "attachment" or not context.bug.attachment:
        return (StepStatus.NOOP, context)

    attachment = context.bug.attachment

    settings = get_settings()
    phabricator_url = attachment.phabricator_url(base_url=settings.phabricator_base_url)

    if not phabricator_url:
        return (StepStatus.NOOP, context)

    description = attachment.description
    if attachment.is_obsolete:
        description = f"{0} - {1}".format("Abandoned", attachment.description)

    issue_key = context.jira.issue

    jira_response = jira_service.client.create_or_update_issue_remote_links(
        issue_key=issue_key,
        global_id=f"{context.bug.id}-{attachment.id}",
        link_url=phabricator_url,
        title=description,
    )

    if jira_response:
        logger.info(
            "Phabricator patch added or updated in Jira issue %s",
            issue_key,
            extra=context.update(operation=Operation.LINK).model_dump(),
        )
        context = context.append_responses(jira_response)
        return (StepStatus.SUCCESS, context)
    else:
        logger.info(
            "Failed to add or update phabricator url in Jira issue %s",
            issue_key,
        )

    return (StepStatus.NOOP, context)


def maybe_delete_duplicate(
    context: ActionContext,
    *,
    bugzilla_service: BugzillaService,
    jira_service: JiraService,
) -> StepResult:
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
        return (StepStatus.SUCCESS, context)
    return (StepStatus.NOOP, context)


def update_issue_summary(
    context: ActionContext, *, jira_service: JiraService
) -> StepResult:
    """Update the Jira issue's summary if the linked bug is modified."""

    if "summary" not in context.event.changed_fields():
        return (StepStatus.NOOP, context)

    jira_response_update = jira_service.update_issue_summary(context)
    context = context.append_responses(jira_response_update)
    return (StepStatus.SUCCESS, context)


def add_jira_comments_for_changes(
    context: ActionContext, *, jira_service: JiraService
) -> StepResult:
    """Add a Jira comment for each field (assignee, status, resolution) change on
    the Bugzilla ticket."""
    comments_responses = jira_service.add_jira_comments_for_changes(context)
    context.append_responses(comments_responses)
    return (StepStatus.SUCCESS, context)


def maybe_assign_jira_user(
    context: ActionContext, *, jira_service: JiraService
) -> StepResult:
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
            return (StepStatus.NOOP, context)

        try:
            resp = jira_service.assign_jira_user(context, bug.assigned_to)  # type: ignore
            context.append_responses(resp)
            return (StepStatus.SUCCESS, context)
        except ValueError as exc:
            logger.info(str(exc), extra=context.model_dump())
            return (StepStatus.INCOMPLETE, context)

    if context.operation == Operation.UPDATE:
        if "assigned_to" not in event.changed_fields():
            return (StepStatus.SUCCESS, context)

        if not bug.is_assigned():
            resp = jira_service.clear_assignee(context)
        else:
            try:
                resp = jira_service.assign_jira_user(context, bug.assigned_to)  # type: ignore
            except ValueError as exc:
                logger.info(str(exc), extra=context.model_dump())
                # If that failed then just fall back to clearing the assignee.
                resp = jira_service.clear_assignee(context)
        context.append_responses(resp)
        return (StepStatus.SUCCESS, context)

    return (StepStatus.NOOP, context)


def _maybe_update_issue_mapped_field(
    source_field: str,
    context: ActionContext,
    parameters: ActionParams,
    jira_service: JiraService,
    wrap_value: Optional[str] = None,
) -> StepResult:
    source_value = getattr(context.bug, source_field, None) or ""
    target_field = getattr(parameters, f"jira_{source_field}_field")
    target_value = getattr(parameters, f"{source_field}_map").get(source_value)

    # If field is empty on create, or update is about another field, then nothing to do.
    if (context.operation == Operation.CREATE and source_value in ["", "---"]) or (
        context.operation == Operation.UPDATE
        and source_field not in context.event.changed_fields()
    ):
        return (StepStatus.NOOP, context)

    if target_value is None:
        logger.info(
            f"Bug {source_field} %r was not in the {source_field} map.",
            source_value,
            extra=context.update(
                operation=Operation.IGNORE,
            ).model_dump(),
        )
        return (StepStatus.INCOMPLETE, context)

    resp = jira_service.update_issue_field(
        context,
        target_field,
        target_value,
        wrap_value,
    )
    context.append_responses(resp)
    return (StepStatus.SUCCESS, context)


def maybe_update_issue_priority(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
    """
    Update the Jira issue priority
    """
    return _maybe_update_issue_mapped_field(
        "priority", context, parameters, jira_service, wrap_value="name"
    )


def maybe_update_issue_resolution(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
    """
    Update the Jira issue status
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    return _maybe_update_issue_mapped_field(
        "resolution", context, parameters, jira_service, wrap_value="name"
    )


def maybe_update_issue_severity(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
    """
    Update the Jira issue severity
    """
    return _maybe_update_issue_mapped_field(
        "severity", context, parameters, jira_service, wrap_value="value"
    )


def maybe_update_issue_points(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
    """
    Update the Jira issue story points
    """
    return _maybe_update_issue_mapped_field(
        "cf_fx_points", context, parameters, jira_service
    )


def maybe_update_issue_status(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
    """
    Update the Jira issue resolution
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    bz_status = context.bug.resolution or context.bug.status
    jira_status = parameters.status_map.get(bz_status or "")

    if jira_status is None:
        logger.info(
            "Bug status %r was not in the status map.",
            bz_status,
            extra=context.update(
                operation=Operation.IGNORE,
            ).model_dump(),
        )
        return (StepStatus.INCOMPLETE, context)

    if context.operation == Operation.CREATE:
        resp = jira_service.update_issue_status(context, jira_status)
        context.append_responses(resp)
        return (StepStatus.SUCCESS, context)

    if context.operation == Operation.UPDATE:
        changed_fields = context.event.changed_fields()

        if "status" in changed_fields or "resolution" in changed_fields:
            resp = jira_service.update_issue_status(context, jira_status)
            context.append_responses(resp)
            return (StepStatus.SUCCESS, context)

    return (StepStatus.NOOP, context)


def maybe_update_components(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
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
        return (StepStatus.NOOP, context)

    # Although we previously introspected the project components, we
    # still have to catch any potential 400 error response here, because
    # the `components` field may not be on the create / update issue.

    if not context.jira.issue:
        raise ValueError("Jira issue unset in Action Context")

    try:
        resp, missing_components = jira_service.update_issue_components(
            context=context,
            components=candidate_components,
            create_components=parameters.jira_components.create_components
        )
    except requests_exceptions.HTTPError as exc:
        if getattr(exc.response, "status_code", None) != 400:
            raise
        # If `components` is not a valid field on create/update screens,
        # then warn developers and ignore the error.
        logger.error(
            f"Could not set components on issue {context.jira.issue}: %s",
            str(exc),
            extra=context.model_dump(),
        )
        context.append_responses(exc.response)
        return (StepStatus.INCOMPLETE, context)

    if missing_components:
        logger.warning(
            "Could not find components '%s' in project",
            ",".join(sorted(missing_components)),
            extra=context.model_dump(),
        )
        return (StepStatus.INCOMPLETE, context)

    context.append_responses(resp)
    return (StepStatus.SUCCESS, context)


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
) -> StepResult:
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
            return (StepStatus.NOOP, context)
    else:
        # On creation, just add them all.
        additions, removals = _build_labels_update(
            added=context.bug.whiteboard, labels_brackets=parameters.labels_brackets
        )

    return _update_issue_labels(context, jira_service, additions, removals)


def sync_keywords_labels(
    context: ActionContext, *, parameters: ActionParams, jira_service: JiraService
) -> StepResult:
    """
    Set keywords as labels on the Jira issue.
    """
    if context.event.changes:
        changes_by_field = {change.field: change for change in context.event.changes}
        if change := changes_by_field.get("keywords"):
            additions = [x.strip() for x in change.added.split(",")]
            removed = [x.strip() for x in change.removed.split(",")]
            removals = sorted(
                set(removed).difference(set(additions))
            )  # sorted for unit testing
        else:
            return (StepStatus.NOOP, context)
    else:
        # On creation, just add them all.
        additions = context.bug.keywords or []
        removals = []

    return _update_issue_labels(context, jira_service, additions, removals)


def _update_issue_labels(
    context: ActionContext,
    jira_service: JiraService,
    additions: Iterable[str],
    removals: Iterable[str],
) -> StepResult:
    if not context.jira.issue:
        raise ValueError("Jira issue unset in Action Context")
    try:
        resp = jira_service.update_issue_labels(
            issue_key=context.jira.issue, add=additions, remove=removals
        )
    except requests_exceptions.HTTPError as exc:
        if getattr(exc.response, "status_code", None) != 400:
            raise
        # If `labels` is not a valid field in this project, then warn developers
        # and ignore the error.
        logger.error(
            f"Could not set labels on issue {context.jira.issue}: %s",
            str(exc),
            extra=context.model_dump(),
        )
        context.append_responses(exc.response)
        return (StepStatus.INCOMPLETE, context)

    context.append_responses(resp)
    return (StepStatus.SUCCESS, context)
