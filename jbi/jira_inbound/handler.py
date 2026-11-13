"""Handle inbound Jira events: correlate, guard, then dispatch to reverse steps.

Every inbound event passes three gates before any reverse step runs:

1. **Echo suppression** (Invariant C) -- an event authored by JBI's own Jira
   service account is dropped, so JBI's forward writes never bounce back.
2. **Correlation** (Invariant B) -- the event must resolve to an existing
   Bugzilla bug through the Jira issue's Bugzilla remote link (or the bug's
   `see_also` as a cross-check). An uncorrelated issue is ignored outright; the
   reverse direction never creates a bug.
3. **Opt-in** -- the bug must match a configured action whose Jira project is
   this issue's project and which has `jira_inbound_enabled` set.

Anything that fails a gate raises `IgnoreInvalidRequestError`, which the
endpoint reports as an ignored event rather than an error: Jira Automation
forwards far more events than JBI acts on, and that is normal traffic.
"""

import logging
from typing import Optional

from statsd.defaults.env import statsd

from jbi import jira
from jbi.bugzilla import models as bugzilla_models
from jbi.bugzilla.client import BugNotAccessibleError
from jbi.bugzilla.service import get_service as get_bugzilla_service
from jbi.environment import get_settings
from jbi.errors import IgnoreInvalidRequestError
from jbi.jira_inbound.models import JiraWebhookRequest
from jbi.jira_steps import ReverseContext, ReverseExecutor
from jbi.models import Action, Actions
from jbi.visibility import bug_restriction_reason
from jbi.writeback import sync_is_stopped

logger = logging.getLogger(__name__)

settings = get_settings()


def _ignore(reason: str, code: str) -> IgnoreInvalidRequestError:
    """Build the "ignored" signal, counting it under a stable reason code.

    `jbi.jira.ignored.count` is the total; `jbi.jira.ignored.<code>.count` says
    *why*, so an ignored-heavy dashboard is explainable rather than a mystery.
    """
    statsd.incr("jbi.jira.ignored.count")
    statsd.incr(f"jbi.jira.ignored.{code}.count")
    return IgnoreInvalidRequestError(reason)


# The account id of the credentials JBI writes to Jira with, looked up once at
# startup by `resolve_bot_identity`. Kept out of `is_bot_authored`'s path so the
# per-event check never makes a network call (the publisher runs it too).
_resolved_bot_account_id: Optional[str] = None


def resolve_bot_identity(
    jira_service: Optional[jira.JiraService] = None,
) -> Optional[str]:
    """Find out which Jira account JBI's credentials belong to, and remember it.

    JBI's own writes come back as events authored by that account, so the
    account has to be recognisable. It is looked up (`myself`) rather than
    configured, so the answer cannot drift from `JIRA_USERNAME`; a configured
    `JIRA_BOT_ACCOUNT_ID` takes precedence and avoids the call.

    Never raises: if Jira is unreachable at startup the email comparison in
    `is_bot_authored` still applies, and read-before-write stops an unsuppressed
    echo from oscillating.
    """
    global _resolved_bot_account_id

    if settings.jira_bot_account_id:
        return settings.jira_bot_account_id

    try:
        me = (jira_service or jira.get_service()).client.myself()
        account_id = me.get("accountId") if isinstance(me, dict) else None
    except Exception:
        logger.warning(
            "Could not look up the Jira account JBI authenticates as; events it "
            "authors are recognised by email only",
            exc_info=True,
        )
        return None

    if not isinstance(account_id, str) or not account_id:
        logger.warning("Jira did not report an account id for JBI's credentials")
        return None

    _resolved_bot_account_id = account_id
    logger.info("JBI's own Jira account id resolved; its events will be ignored")
    return account_id


def is_bot_authored(event: JiraWebhookRequest) -> bool:
    """Return True when JBI's own Jira account caused this event.

    Half of Invariant C. The other half lives on the Bugzilla side
    (`jbi.runner`, D6b) -- a loop has two directions and both must be closed.

    The account is recognised two ways, because a payload may carry either:
    its account id (configured, or looked up from the credentials at startup),
    and its email, which is `JIRA_USERNAME`. Neither makes a network call.
    """
    bot_account_id = settings.jira_bot_account_id or _resolved_bot_account_id
    if bot_account_id and event.actor_account_id == bot_account_id:
        return True

    bot_email = (settings.jira_username or "").strip().lower()
    return "@" in bot_email and event.actor_email == bot_email


def correlate_bug_id(
    issue_key: str, jira_service: Optional[jira.JiraService] = None
) -> Optional[int]:
    """Return the Bugzilla bug id linked to this Jira issue, or `None`.

    Correlation uses the Bugzilla remote link that JBI itself writes on every
    issue it creates (`add_link_to_bugzilla`, whose `globalId` is the bug id).
    """
    service = jira_service or jira.get_service()
    return service.get_linked_bugzilla_bug_id(issue_key)


def find_inbound_action(
    bug: bugzilla_models.Bug, project_key: str, actions: Actions
) -> Optional[Action]:
    """Return the action that opted this bug's project into inbound sync.

    Reuses the forward path's whiteboard-tag matching so a bug is governed by
    the same action in both directions, then narrows to the Jira project the
    event came from.
    """
    from jbi.runner import lookup_actions

    try:
        candidates = lookup_actions(bug, actions)
    except Exception:
        return None

    for action in candidates:
        if (
            action.jira_project_key == project_key
            and action.parameters.jira_inbound_enabled
        ):
            return action
    return None


def execute_jira_event(event: JiraWebhookRequest, actions: Actions) -> dict:
    """Run the reverse pipeline for an inbound Jira event."""
    issue = event.issue
    issue_key = issue.key if issue else None
    if not issue_key:
        raise _ignore("no issue key in payload", "no_issue_key")

    project_key = issue.project_key if issue else None
    if not project_key:
        raise _ignore(f"cannot determine project of issue {issue_key}", "no_project")

    # Gate 1: echo suppression (Invariant C).
    if is_bot_authored(event):
        raise _ignore(
            f"ignore event on {issue_key} authored by JBI itself", "bot_authored"
        )

    # Gate 2: correlation (Invariant B).
    bug_id = correlate_bug_id(issue_key)
    if bug_id is None:
        raise _ignore(f"no Bugzilla bug linked to issue {issue_key}", "uncorrelated")

    bugzilla_service = get_bugzilla_service()
    try:
        bug = bugzilla_service.client.get_bug(bug_id)
    except BugNotAccessibleError as err:
        raise _ignore(
            f"bug {bug_id} is not accessible: {err}", "bug_not_accessible"
        ) from err

    if reason := bug_restriction_reason(bug):
        # Same definition of "restricted" as the forward path, and the same
        # consequence: no field of a security or embargoed bug is touched,
        # not just its comments.
        raise _ignore(f"bug {bug_id} is restricted: {reason}", "bug_restricted")

    # Cross-check the link in the other direction. A one-sided link means the
    # bug was re-pointed at a different issue, and writing to it would put the
    # change on the wrong bug.
    linked_key = bug.extract_from_see_also(project_key=project_key)
    if linked_key and linked_key != issue_key:
        raise _ignore(
            f"bug {bug_id} links to issue {linked_key!r}, not {issue_key!r}",
            "link_mismatch",
        )

    # Gate 3: the bug's action must have opted into inbound sync.
    action = find_inbound_action(bug, project_key, actions)
    if action is None:
        raise _ignore(
            f"no action with `jira_inbound_enabled` for bug {bug_id} "
            f"in project {project_key}",
            "no_inbound_action",
        )

    # Same label, same meaning, other direction. Labels come from the payload
    # when the rule sends them, and are fetched otherwise -- an absent
    # `labels` key must not read as "no stop label".
    stop_label = action.parameters.sync_stop_label
    if stop_label:
        fields = issue.fields if issue else None
        labels = fields.labels if fields and fields.labels is not None else None
        if labels is None:
            labels = jira.get_service().get_issue_labels(None, issue_key)
        if sync_is_stopped(labels, stop_label):
            statsd.incr("jbi.sync_stopped.count")
            raise _ignore(
                f"sync stopped by the {stop_label!r} label on {issue_key}",
                "stop_label",
            )

    if (
        event.lacks_needed_changelog
        and not action.parameters.reverse_sync_without_changelog
    ):
        # Loud rather than silent: without this the event is "handled" and
        # every writer NOOPs, which looks like success and syncs nothing.
        raise _ignore(
            f"payload for {issue_key} carries no changelog, so JBI cannot "
            "tell which field changed; configure the Automation rule to send "
            "one, or set `reverse_sync_without_changelog`",
            "no_changelog",
        )

    context = ReverseContext(
        action=action,
        bug=bug,
        issue_key=issue_key,
        event=event,
    )
    logger.info(
        "Handling inbound Jira event on %s for Bug %s",
        issue_key,
        bug.id,
        extra=context.model_dump(),
    )
    details = ReverseExecutor(bugzilla_service=bugzilla_service)(context)
    statsd.incr("jbi.jira.processed.count")
    return details
