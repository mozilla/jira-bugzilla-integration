"""Field-ownership policy for the Jira -> BMO direction (plan section 4).

Two separate rules live here, deliberately in one module so the whole policy
can be audited by reading one file rather than by tracing every writer:

**What may be written at all.** BMO owns the *technical reality* of the work;
Jira owns *how that work is planned and delivered*. Planning fields have no
BMO equivalent, and writing them back would either fail or pollute a public
bug with internal planning context. They are therefore never written.

**Who wins when both sides changed.** For the execution fields that do sync
both ways, a same-window conflict resolves to the BMO value, because BMO is
where the engineering truth lives.
"""

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Execution fields: synced both ways, BMO authoritative on conflict.
WRITEBACK_ALLOWLIST = frozenset(
    {"status", "resolution", "priority", "assignee", "summary", "comment"}
)

# Planning fields: Jira-owned, never written back (R-07). Names are Jira's
# changelog field labels, lowercased. This list is explicit rather than
# "everything not in the allowlist" so that adding a reverse writer for a
# planning field requires deleting a line here, in review.
WRITEBACK_DENYLIST = frozenset(
    {
        "sprint",
        "story points",
        "epic link",
        "epic",
        "parent",
        "rank",
        "fix version",
        "fixversions",
        "labels",
        "components",
        "issuetype",
        "duedate",
    }
)


def normalize_field(field: Optional[str]) -> str:
    """Normalize a Jira field name for policy lookups."""
    return (field or "").strip().lower()


def is_writeback_allowed(field: Optional[str]) -> bool:
    """Return True when this Jira field may be written back to BMO."""
    name = normalize_field(field)
    if name in WRITEBACK_DENYLIST:
        return False
    return name in WRITEBACK_ALLOWLIST


def suppressed_fields(fields: Iterable[Optional[str]]) -> list[str]:
    """Return the changed fields this policy refuses to write back.

    Used for logging: "this event changed Sprint and status; Sprint was
    suppressed" is the observable evidence that R-07 holds.
    """
    return [
        normalize_field(field)
        for field in fields
        if normalize_field(field) in WRITEBACK_DENYLIST
    ]


def bmo_wins_conflict(
    jira_previous_value: Optional[str], bmo_current_value: Optional[str]
) -> bool:
    """Return True when BMO changed independently and must not be overwritten.

    A Jira changelog entry says what the value was *before* this change. While
    the two systems are in sync, that previous value equals the bug's current
    value. If it does not, BMO was edited in the same window, and the plan's
    conflict rule -- BMO wins for execution fields -- means this write is
    dropped rather than clobbering the newer engineering truth.

    An unknown previous value (`None`) means the conflict cannot be detected,
    so the write proceeds: the alternative would silently stop syncing
    whenever Jira omits changelog detail.
    """
    if jira_previous_value is None:
        return False
    return (jira_previous_value or "").strip() != (bmo_current_value or "").strip()


# --- The sync-stop label ----------------------------------------------------


def sync_is_stopped(labels: Iterable[Optional[str]], stop_label: Optional[str]) -> bool:
    """Return True when a Jira issue carries the configured stop label.

    A deliberate human override: someone has decided this pair should stop
    syncing, and that decision outranks every other rule here. It halts both
    directions, because "stop syncing this" is what a user means by it, and a
    label that stopped only one direction would be a trap.

    Removing the label resumes syncing from the *current* state -- changes
    made while stopped are not replayed, since nothing records them.

    Comparison is case-insensitive: Jira preserves label case, and a user
    typing `JBI-Sync-Stop` plainly means the same thing.
    """
    if not stop_label:
        return False
    wanted = stop_label.strip().lower()
    return any(str(label).strip().lower() == wanted for label in labels if label)
