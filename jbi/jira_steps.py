"""Reverse step functions (Jira -> BMO) and the executor that runs them.

This is the mirror image of `jbi/steps.py`. The asymmetry is deliberate and is
Invariant B of the plan: the forward direction may CREATE or UPDATE a Jira
issue, while the reverse direction is UPDATE-only and can never create a
Bugzilla bug.

Unlike forward steps, reverse steps are not listed per action in config. They
are a fixed pipeline, and each step self-gates on the action's parameters, so
that enabling the inbound path cannot accidentally enable a write the action
did not configure.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable, Optional

from requests import exceptions as requests_exceptions

from jbi.bugzilla.models import Bug
from jbi.identity import UNASSIGNED_EMAIL, get_identity_map
from jbi.jira_inbound.models import JiraWebhookRequest
from jbi.models import Action, Context
from jbi.sync_markers import was_written_by_forward_sync
from jbi.visibility import can_copy_jira_text_to_bug
from jbi.writeback import (
    bmo_wins_conflict,
    is_writeback_allowed,
    suppressed_fields,
)

if TYPE_CHECKING:
    from jbi.bugzilla.service import BugzillaService

logger = logging.getLogger(__name__)


class ReverseStepStatus(Enum):
    """Result of executing a reverse step.

    SUCCESS: the step wrote to BMO.
    NOOP: nothing to do (not configured, field unchanged, value already equal).
    INCOMPLETE: an anticipated failure, eg. an unresolvable identity.
    """

    SUCCESS = auto()
    NOOP = auto()
    INCOMPLETE = auto()


class ReverseContext(Context, extra="forbid"):
    """Logging context for the Jira -> BMO direction."""

    action: Action
    bug: Bug
    issue_key: str
    event: JiraWebhookRequest
    current_step: Optional[str] = None
    responses: list = []

    def append_responses(self, *responses):
        """Return a copy with the given responses recorded."""
        return self.update(responses=[*self.responses, *responses])


ReverseStepResult = tuple[ReverseStepStatus, ReverseContext]
ReverseStep = Callable[..., ReverseStepResult]

# --- Reverse status & resolution mapping (plan section 4.1) ----------------
#
# `status_map` (BMO -> Jira) is many-to-one -- in prod, ten BMO
# status/resolution values collapse onto a single Jira status -- so it cannot
# be inverted. Reverse status instead derives from Jira's built-in status
# category, which exists with the same three values in every project no matter
# how its workflow is named. That gives one project-agnostic default map
# instead of one hand-maintained map per action.
REVERSE_STATUS_CATEGORY_MAP = {
    "indeterminate": "ASSIGNED",
    "done": "RESOLVED",
}

REOPENED_STATUS = "REOPENED"
RESOLVED_STATUSES = {"RESOLVED", "VERIFIED", "CLOSED"}

# The `new` category is deliberately absent from the map above: reverse status
# only ever moves a bug *forward*.
#
# Real workflows put more than "not started" in that category -- the pilot
# project's `new`-category statuses are Backlog, To Do and **Blocked**. So
# moving an issue from In Progress to Blocked in Jira would otherwise write
# NEW over ASSIGNED and regress the bug to "never worked on", which is
# actively wrong rather than merely coarse. BMO does not model "blocked" as a
# status at all (it uses `depends_on`), so there is nothing to mirror.
#
# The one `new`-category transition worth writing is a genuine reopen: an
# issue leaving a done state, where BMO's REOPENED preserves the fact that
# the bug was once closed.


def reverse_status_for(context: ReverseContext) -> Optional[str]:
    """Return the BMO status to write, or `None` to leave the status alone."""
    category = context.event.issue.status_category if context.event.issue else None
    if not category:
        return None

    overrides = context.action.parameters.reverse_status_overrides
    if category in overrides:
        return overrides[category]

    if category == "new":
        if (context.bug.status or "") in RESOLVED_STATUSES:
            return REOPENED_STATUS
        return None

    return REVERSE_STATUS_CATEGORY_MAP.get(category)


def invert_resolution_map(resolution_map: dict[str, str]) -> dict[str, str]:
    """Invert a BMO -> Jira resolution map.

    Safe because `ActionParams` rejects a non-injective `resolution_map` at
    config load; this is the consumer that validation exists for.
    """
    return {jira: bmo for bmo, jira in resolution_map.items()}


def reverse_resolution_for(context: ReverseContext) -> Optional[str]:
    """Return the BMO resolution to write, or `None` to leave it untouched.

    Precedence, deliberately conservative because a resolution is a factual
    claim about *why* a bug is closed:

    1. Jira's resolution field, mapped back through the inverted
       `resolution_map`.
    2. the action's `default_reverse_resolution`.
    3. nothing -- write the status, leave the resolution alone, and let the
       reconciliation report (R-13) surface it for a human. Never guess:
       a wrong DUPLICATE or WONTFIX misleads everyone who reads the bug later.
    """
    fields = context.event.issue.fields if context.event.issue else None
    jira_resolution = fields.resolution.name if fields and fields.resolution else None

    if jira_resolution:
        inverted = invert_resolution_map(context.action.parameters.resolution_map)
        if jira_resolution in inverted:
            return inverted[jira_resolution]
        logger.info(
            "Jira resolution %r is not in the inverted resolution_map of %r",
            jira_resolution,
            context.action.whiteboard_tag,
            extra=context.model_dump(),
        )

    return context.action.parameters.default_reverse_resolution


def invert_priority_map(priority_map: dict[str, str]) -> dict[str, str]:
    """Invert a BMO -> Jira priority map, first mapping wins.

    Unlike `resolution_map` this one is *not* required to be injective: the
    default maps both "" and "--" onto Jira's "None", and both mean "unset" in
    BMO, so collapsing them is harmless. First-wins keeps the choice
    deterministic instead of dict-ordering-dependent in a surprising way.
    """
    inverted: dict[str, str] = {}
    for bmo, jira in priority_map.items():
        inverted.setdefault(jira, bmo)
    return inverted


# --- Reverse steps ----------------------------------------------------------


def _changed(context: ReverseContext, field: str) -> bool:
    """Return True when the event changed a field we are allowed to write.

    Two rules in one place: reverse steps act only on what actually changed
    (writing every field on every event would let a Jira edit of one field
    overwrite BMO values a human had just set by hand), and only on fields
    the write-back policy allows (`jbi.writeback`).
    """
    if not is_writeback_allowed(field):
        return False
    if not context.event.has_changelog:
        # Opted in via `reverse_sync_without_changelog` (the handler rejects
        # the event otherwise): treat the payload as a full-state push. Note
        # this also disables conflict detection, since there is no previous
        # value to compare against.
        return context.action.parameters.reverse_sync_without_changelog
    return field in context.event.changed_fields()


def _previous_jira_value(context: ReverseContext, field: str) -> Optional[str]:
    """Return what the field was before this change, per Jira's changelog."""
    if not context.event.changelog:
        return None
    for item in context.event.changelog.items:
        if item.field == field:
            return item.fromString
    return None


def _skip_on_conflict(
    context: ReverseContext, field: str, bmo_current: Optional[str]
) -> bool:
    """Return True when BMO changed independently, so BMO wins (section 4).

    Logged rather than silent: a dropped write is exactly the kind of thing
    someone will later ask "why didn't that sync?" about.
    """
    previous = _previous_jira_value(context, field)
    if not bmo_wins_conflict(previous, bmo_current):
        return False

    logger.info(
        "Bug %s %s changed independently (BMO %r, Jira had %r); "
        "BMO wins, not overwriting",
        context.bug.id,
        field,
        bmo_current,
        previous,
        extra=context.model_dump(),
    )
    return True


def resolve_bmo_assignee(context: ReverseContext) -> Optional[str]:
    """Resolve the issue's Jira assignee to a BMO email, or `None`.

    Identity map first (the only source that can name someone whose Jira
    email is hidden), then the email in the payload. `None` means "could not
    resolve" -- never a guess.
    """
    fields = context.event.issue.fields if context.event.issue else None
    assignee = fields.assignee if fields else None
    if assignee is None or not assignee.accountId:
        return None
    return get_identity_map().bmo_email_for(assignee.accountId) or assignee.emailAddress


def writeback_status(
    context: ReverseContext, *, bugzilla_service: BugzillaService
) -> ReverseStepResult:
    """Write the Jira status (and resolution) back to BMO."""
    if not (_changed(context, "status") or _changed(context, "resolution")):
        return (ReverseStepStatus.NOOP, context)

    category = context.event.issue.status_category if context.event.issue else None
    if not category:
        # No category means we cannot reason about the status at all: either
        # the Automation rule does not send it, or the payload is malformed.
        logger.info(
            "Issue %s carries no status category, not writing status back",
            context.issue_key,
            extra=context.model_dump(),
        )
        return (ReverseStepStatus.INCOMPLETE, context)

    status = reverse_status_for(context)
    if not status:
        # A deliberate no-op, not a failure: see REVERSE_STATUS_CATEGORY_MAP.
        logger.info(
            "Issue %s moved to a %r-category status and Bug %s is not "
            "resolved; leaving its status alone rather than regressing it",
            context.issue_key,
            category,
            context.bug.id,
            extra=context.model_dump(),
        )
        return (ReverseStepStatus.NOOP, context)

    resolution = None
    if status in RESOLVED_STATUSES:
        resolution = reverse_resolution_for(context)
        if resolution is None:
            logger.warning(
                "Issue %s closed but no BMO resolution could be determined; "
                "writing status only",
                context.issue_key,
                extra=context.model_dump(),
            )
    else:
        # Moving a bug out of a resolved state must clear its resolution,
        # otherwise BMO shows an open bug that still claims to be FIXED.
        resolution = "" if context.bug.resolution else None

    # BMO rejects ASSIGNED on a bug with no assignee, so the assignee has to
    # travel with the status. Confirmed against the dev instance: the same
    # write 400s alone and succeeds when `assigned_to` is included.
    assigned_to = None
    if status == "ASSIGNED" and not context.bug.is_assigned():
        assigned_to = resolve_bmo_assignee(context)
        if not assigned_to:
            logger.warning(
                "Issue %s is in progress but Bug %s is unassigned and the "
                "Jira assignee could not be resolved; BMO cannot represent "
                "this state, so the status is left alone",
                context.issue_key,
                context.bug.id,
                extra=context.model_dump(),
            )
            return (ReverseStepStatus.INCOMPLETE, context)

    response = bugzilla_service.set_status_resolution(
        context.bug, status, resolution, assigned_to=assigned_to
    )
    if response is None:
        return (ReverseStepStatus.NOOP, context)
    return (ReverseStepStatus.SUCCESS, context.append_responses(response))


def writeback_priority(
    context: ReverseContext, *, bugzilla_service: BugzillaService
) -> ReverseStepResult:
    """Write the Jira priority back to BMO."""
    if not _changed(context, "priority"):
        return (ReverseStepStatus.NOOP, context)

    fields = context.event.issue.fields if context.event.issue else None
    jira_priority = fields.priority.name if fields and fields.priority else None
    if not jira_priority:
        return (ReverseStepStatus.NOOP, context)

    inverted = invert_priority_map(context.action.parameters.priority_map)
    bmo_priority = inverted.get(jira_priority)
    if bmo_priority is None:
        logger.info(
            "Jira priority %r has no BMO equivalent for %r",
            jira_priority,
            context.action.whiteboard_tag,
            extra=context.model_dump(),
        )
        return (ReverseStepStatus.INCOMPLETE, context)

    previous_jira = _previous_jira_value(context, "priority")
    previous_bmo = inverted.get(previous_jira) if previous_jira else None
    if previous_bmo is not None and bmo_wins_conflict(
        previous_bmo, context.bug.priority
    ):
        logger.info(
            "Bug %s priority changed independently (BMO %r); BMO wins",
            context.bug.id,
            context.bug.priority,
            extra=context.model_dump(),
        )
        return (ReverseStepStatus.NOOP, context)

    response = bugzilla_service.set_priority(context.bug, bmo_priority)
    if response is None:
        return (ReverseStepStatus.NOOP, context)
    return (ReverseStepStatus.SUCCESS, context.append_responses(response))


def writeback_assignee(
    context: ReverseContext, *, bugzilla_service: BugzillaService
) -> ReverseStepResult:
    """Write the Jira assignee back to BMO, resolving the person first."""
    if not _changed(context, "assignee"):
        return (ReverseStepStatus.NOOP, context)

    fields = context.event.issue.fields if context.event.issue else None
    assignee = fields.assignee if fields else None

    if assignee is None or not assignee.accountId:
        # Unassigned in Jira: mirror that with BMO's sentinel rather than
        # leaving a stale name on the bug.
        email: Optional[str] = UNASSIGNED_EMAIL
    else:
        # Tier 1: the override map. Tier 2: the email Jira gave us, when it
        # is not hidden. Tier 3: leave the assignee alone -- never guess.
        email = resolve_bmo_assignee(context)
        if not email:
            logger.info(
                "Could not resolve Jira account %s to a BMO user; "
                "leaving the assignee of Bug %s unchanged",
                assignee.accountId,
                context.bug.id,
                extra=context.model_dump(),
            )
            return (ReverseStepStatus.INCOMPLETE, context)

    response = bugzilla_service.set_assignee(context.bug, email)
    if response is None:
        return (ReverseStepStatus.NOOP, context)
    return (ReverseStepStatus.SUCCESS, context.append_responses(response))


def writeback_summary(
    context: ReverseContext, *, bugzilla_service: BugzillaService
) -> ReverseStepResult:
    """Write the Jira summary back to the BMO bug's summary."""
    if not _changed(context, "summary"):
        return (ReverseStepStatus.NOOP, context)

    fields = context.event.issue.fields if context.event.issue else None
    summary = fields.summary if fields else None
    if not summary:
        return (ReverseStepStatus.NOOP, context)

    # A summary is free text too: the title of an embargoed issue can itself
    # describe an unpublished vulnerability.
    if can_copy_jira_text_to_bug(context.bug, context.event):
        return (ReverseStepStatus.INCOMPLETE, context)

    if _skip_on_conflict(context, "summary", context.bug.summary):
        return (ReverseStepStatus.NOOP, context)

    response = bugzilla_service.set_summary(context.bug, summary)
    if response is None:
        return (ReverseStepStatus.NOOP, context)
    return (ReverseStepStatus.SUCCESS, context.append_responses(response))


# BMO rejects oversized comments; leave room for the attribution prefix and
# the truncation marker rather than losing the whole comment.
BMO_COMMENT_MAX_LENGTH = 65535
TRUNCATION_MARKER = "\n[... truncated, see the Jira issue for the full comment]"


def _comment_author_name(context: ReverseContext) -> str:
    """Return the display name to attribute a copied comment to.

    Identity map first (it is the only source that can name someone whose
    Jira email is hidden), then the payload's display name. JBI posts as its
    own service account rather than impersonating anyone, so this name only
    ever appears inside the comment text.
    """
    author = context.event.comment.author if context.event.comment else None
    if author and author.accountId:
        mapped = get_identity_map().display_name_for(author.accountId)
        if mapped:
            return mapped
    if author and author.displayName:
        return author.displayName
    return "unknown"


def format_comment(context: ReverseContext) -> Optional[str]:
    """Render the BMO comment text for an inbound Jira comment."""
    comment = context.event.comment
    body = comment.body if comment else None
    if not body:
        return None

    prefix = f"from Jira, by {_comment_author_name(context)}:\n"
    budget = BMO_COMMENT_MAX_LENGTH - len(prefix) - len(TRUNCATION_MARKER)
    if len(body) > budget:
        body = body[:budget] + TRUNCATION_MARKER
    return prefix + body


def writeback_comment(
    context: ReverseContext, *, bugzilla_service: BugzillaService
) -> ReverseStepResult:
    """Copy a Jira comment onto the linked bug, if the bug's audience allows.

    The "from Jira, by <name>" prefix does double duty: it satisfies the
    PRD's attribution requirement, and it makes a copied comment recognisable
    rather than looking like something the service account said itself.
    """
    if not context.event.comment:
        return (ReverseStepStatus.NOOP, context)

    if not context.action.parameters.reverse_comment_sync_enabled:
        return (ReverseStepStatus.NOOP, context)

    if was_written_by_forward_sync(context.event.comment.body):
        # JBI put this comment on the issue in the first place, copied from
        # the bug. Re-importing it would nest the attribution and grow the
        # text on every hop -- see `jbi.sync_markers`.
        logger.info(
            "Comment on %s was written by JBI's forward sync; not copying it "
            "back to Bug %s",
            context.issue_key,
            context.bug.id,
            extra=context.model_dump(),
        )
        return (ReverseStepStatus.NOOP, context)

    if can_copy_jira_text_to_bug(context.bug, context.event):
        return (ReverseStepStatus.INCOMPLETE, context)

    text = format_comment(context)
    if not text:
        return (ReverseStepStatus.NOOP, context)

    response = bugzilla_service.add_comment(context.bug, text)
    if response is None:
        return (ReverseStepStatus.NOOP, context)
    return (ReverseStepStatus.SUCCESS, context.append_responses(response))


# The reverse pipeline, in execution order.
REVERSE_STEPS: list[ReverseStep] = [
    writeback_status,
    writeback_priority,
    writeback_assignee,
    writeback_summary,
    writeback_comment,
]


class ReverseExecutor:
    """Runs the reverse steps for one action against one inbound event."""

    def __init__(self, bugzilla_service: Optional[BugzillaService] = None):
        if bugzilla_service is None:
            from jbi.bugzilla.service import get_service as get_bugzilla_service

            bugzilla_service = get_bugzilla_service()
        self.bugzilla_service = bugzilla_service

    def __call__(self, context: ReverseContext) -> dict:
        results: dict[str, str] = {}

        # R-07: planning fields are Jira's to own. Logged rather than dropped
        # in silence, so "the epic moved and BMO did not change" is
        # observable instead of merely asserted.
        suppressed = suppressed_fields(context.event.changed_fields())
        if suppressed:
            logger.info(
                "Ignoring Jira-owned fields %s on issue %s",
                ", ".join(suppressed),
                context.issue_key,
                extra=context.model_dump(),
            )

        for step in REVERSE_STEPS:
            context = context.update(current_step=step.__name__)
            try:
                status, context = step(
                    context=context, bugzilla_service=self.bugzilla_service
                )
            except requests_exceptions.HTTPError as exc:
                code = getattr(exc.response, "status_code", None)
                if code is None or not (400 <= code < 500):
                    # 5xx or a connection problem: a real outage, which the
                    # caller should see rather than have swallowed.
                    raise
                # A 4xx means BMO rejected *this* write as invalid. Retrying
                # will not help, and one rejected field must not discard the
                # rest of the pipeline -- a failed status write should still
                # let the comment through.
                logger.warning(
                    "Reverse step %s rejected by Bugzilla (HTTP %s) for "
                    "issue %s / Bug %s; continuing with the remaining steps",
                    step.__name__,
                    code,
                    context.issue_key,
                    context.bug.id,
                    extra=context.model_dump(),
                )
                results[step.__name__] = ReverseStepStatus.INCOMPLETE.name
                continue
            results[step.__name__] = status.name
            logger.info(
                "Reverse step %s -> %s for issue %s / Bug %s",
                step.__name__,
                status.name,
                context.issue_key,
                context.bug.id,
                extra=context.model_dump(),
            )
        return {"steps": results, "responses": context.responses}
