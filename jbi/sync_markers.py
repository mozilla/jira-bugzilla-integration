"""Recognise text JBI itself wrote, in either system.

The identity gates (Invariant C) are the primary loop protection, but they
depend on configuration: if `bugzilla_bot_login` or `jira_bot_account_id` is
unset or wrong, JBI's own writes come back at it. For *fields* that is
harmless -- read-before-write makes the second write a no-op. For *comments*
it is not, because each hop rewraps the text in another attribution layer:

    testing comment
    -> *someone* commented: from Jira, by Someone: testing comment
    -> from Jira, by Someone: *someone* commented: from Jira, by Someone: ...

The text differs every time, so no duplicate check can catch it, and the
comment grows without bound. Observed live.

So comments carry a marker and each direction refuses to re-import the
other's. This is defence in depth, independent of identity config.
"""

import json
import re
from typing import Optional

# What the reverse direction writes onto a Bugzilla bug.
REVERSE_COMMENT_PREFIX = "from Jira, by "

# What the forward direction writes onto a Jira issue
# (`JiraService.add_jira_comment`: "*<login>* commented: \n<body>").
FORWARD_COMMENT_RE = re.compile(r"^\*[^*]+\*\s+commented:", re.MULTILINE)


def was_written_by_reverse_sync(body: Optional[str]) -> bool:
    """True when this Bugzilla comment was copied from Jira by JBI."""
    if not body:
        return False
    return body.lstrip().startswith(REVERSE_COMMENT_PREFIX)


# Jira re-renders wiki markup on the way out, so the marker JBI wrote is not
# the marker it reads back: `*someone@example.com* commented:` comes back as
# `_[mailto:someone@example.com]_ commented:`. Matching only the written form
# lets the rendered one through, which is how the loop survived the first
# attempt at this breaker.
RENDERED_FORWARD_RE = re.compile(r"^_?\[?mailto:[^\]]*\]?_?\s+commented:")


# `JiraService.add_jira_comments_for_changes` posts a JSON blob rather than
# prose, so it carries neither marker above:
#     {"modified by": "someone@example.com", "resolution": "", "status": "ASSIGNED"}
# Copying that back onto the bug duplicates a change the bug already records
# in its own history -- observed as a status change "posting twice".
FORWARD_CHANGE_KEYS = {"modified by", "resolution", "status", "assignee"}


def _is_forward_change_comment(text: str) -> bool:
    """True for the JSON blob the forward path posts for field changes."""
    if not text.lstrip().startswith("{"):
        return False
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    return (
        isinstance(parsed, dict)
        and bool(parsed)
        and set(parsed).issubset(FORWARD_CHANGE_KEYS)
    )


def was_written_by_forward_sync(body: Optional[str]) -> bool:
    """True when this Jira comment originated from JBI rather than a human.

    Three signals, in increasing order of robustness:

    1. the marker as JBI writes it;
    2. the same marker as Jira renders it back;
    3. the JSON blob the forward path posts for field changes;
    4. **our own reverse-sync prefix appearing anywhere in the text.** That
       phrase only exists because JBI put it on a bug, so finding it in a
       Jira comment means the text has already round-tripped. This is the
       one that does not depend on how Jira formats anything.
    """
    if not body:
        return False
    text = body.lstrip()
    if FORWARD_COMMENT_RE.match(text):
        return True
    if RENDERED_FORWARD_RE.match(text):
        return True
    if _is_forward_change_comment(text):
        return True
    return REVERSE_COMMENT_PREFIX in text
