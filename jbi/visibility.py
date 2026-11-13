"""Decide whether JBI may write Jira content back onto a Bugzilla bug (R-12).

Comments are the risky direction: Jira issues live in an internal planning
tool, Bugzilla bugs are frequently world-readable, and a comment copied from
one to the other cannot be un-published. The rule is therefore conservative --
JBI copies free text only when both ends allow it: the bug's audience must be
one JBI can reason about, and the Jira content must carry no restriction of
its own.
"""

import logging
from typing import Optional

from jbi.bugzilla.models import Bug
from jbi.jira_inbound.models import JiraWebhookRequest

logger = logging.getLogger(__name__)


def bug_restriction_reason(bug: Bug) -> Optional[str]:
    """Return why a bug is restricted, or `None` when it is public.

    Two signals, because either alone is insufficient: `is_private` is a
    payload-level flag that BMO may omit (the field is optional, so an absent
    value would otherwise read as "public"), and `groups` is the actual
    mechanism behind security, embargoed and employee-only bugs.
    """
    if bug.is_private:
        return "bug is private"
    if bug.groups:
        return f"bug is restricted to groups: {', '.join(str(g) for g in bug.groups)}"
    return None


# --- Jira-side confidentiality --------------------------------------------
#
# The mirror of the checks above, and the more dangerous direction: a
# Bugzilla bug is frequently world-readable, so copying an embargoed Jira
# comment onto one publishes it irrevocably.
#
# These fail *closed*. The Automation rule's payload is assembled by a rule
# JBI does not control, so "the field is absent" cannot be read as "there is
# no restriction" -- it is equally consistent with a rule that was never
# configured to send it. Copying Jira text to BMO therefore requires the
# action to opt in via `reverse_comment_sync_enabled`, which is an operator
# asserting the rule sends these fields.


def jira_comment_restriction_reason(event: JiraWebhookRequest) -> Optional[str]:
    """Return why an inbound Jira comment is confidential, or `None`."""
    comment = event.comment
    if comment is None:
        return None
    if comment.visibility is not None and any(
        (
            comment.visibility.value,
            comment.visibility.type,
            comment.visibility.identifier,
        )
    ):
        target = comment.visibility.value or comment.visibility.type or "a role/group"
        return f"Jira comment is restricted to {target}"
    if comment.jsdPublic is False:
        return "Jira comment is internal-only (jsdPublic=false)"
    if comment.internal is True:
        return "Jira comment is internal-only (internal=true)"
    return None


def jira_issue_restriction_reason(event: JiraWebhookRequest) -> Optional[str]:
    """Return why an inbound Jira issue is embargoed, or `None`.

    An issue security level is Jira's embargo marker. When one is set, no
    free text from that issue may reach BMO -- not its comments, and not its
    summary, which can itself describe an unpublished vulnerability.
    """
    issue = event.issue
    fields = issue.fields if issue else None
    security = fields.security if fields else None
    if security is not None and (security.name or security.id):
        name = security.name or security.id or "restricted"
        return f"Jira issue has security level {name!r}"
    return None


def can_copy_jira_text_to_bug(bug: Bug, event: JiraWebhookRequest) -> Optional[str]:
    """Return the reason free text must not be copied, or `None` if it may.

    Checks both ends: the bug's audience (can JBI reason about who will read
    this?) and the Jira content's own classification.
    """
    for reason in (
        bug_restriction_reason(bug),
        jira_issue_restriction_reason(event),
        jira_comment_restriction_reason(event),
    ):
        if reason:
            logger.info(
                "Not copying Jira text to Bug %s: %s",
                bug.id,
                reason,
                extra={"bug": {"id": bug.id}},
            )
            return reason
    return None
