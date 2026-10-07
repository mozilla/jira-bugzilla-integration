"""Content-based loop protection for comments.

The identity gates are the primary defence, but they depend on
configuration. Comments need a second line because, unlike field writes,
each echo *rewrites* the text -- so no duplicate check can catch it and the
comment grows without bound. This was observed live before the breakers
existed.
"""

import json

import pytest

from jbi import Operation, steps
from jbi.jira import JiraService
from jbi.sync_markers import (
    was_written_by_forward_sync,
    was_written_by_reverse_sync,
)

# Captured verbatim from the live runaway.
HOP_1 = (
    "*jgauf@mozilla.com* commented: \nfrom Jira, by John Gauf: testing comment number 2"
)
HOP_2 = (
    "*jgauf@mozilla.com* commented: \nfrom Jira, by John Gauf: "
    "_[mailto:jgauf@mozilla.com]_ commented: from Jira, by John Gauf: "
    "testing comment number 2"
)


@pytest.mark.parametrize("body", [HOP_1, HOP_2])
def test_forward_written_comments_are_recognised(body):
    assert was_written_by_forward_sync(body) is True


def test_reverse_written_comments_are_recognised():
    assert was_written_by_reverse_sync("from Jira, by John Gauf:\nhello") is True


@pytest.mark.parametrize(
    "body",
    [
        "an ordinary human comment",
        "discussion of *emphasis* in the middle",
        "",
        None,
    ],
)
def test_human_comments_are_not_mistaken_for_sync_output(body):
    assert was_written_by_forward_sync(body) is False
    assert was_written_by_reverse_sync(body) is False


def test_forward_sync_skips_a_comment_it_imported_from_jira(
    action_context_factory, mocked_jira, capturelogs
):
    """Bugzilla side of the breaker."""
    import logging

    context = action_context_factory(
        operation=Operation.COMMENT,
        bug__with_comment=True,
        bug__comment__body="from Jira, by John Gauf:\ntesting comment number 2",
        bug__comment__id=7,
        bug__comment__is_private=False,
        event__target="comment",
        jira__issue="JBI-234",
        current_step="create_comment",
    )

    with capturelogs.for_logger("jbi.steps").at_level(logging.INFO):
        result, _ = steps.create_comment(context, jira_service=JiraService(mocked_jira))

    assert result == steps.StepStatus.NOOP
    assert not mocked_jira.issue_add_comment.called
    assert any("reverse sync" in r.message for r in capturelogs.records)


# --- Jira re-renders the marker it was given --------------------------------

# JBI writes `*someone@example.com* commented:` into Jira; Automation's
# `{{comment.body}}` reads it back as `_[mailto:someone@example.com]_
# commented:`. Captured from a live loop -- the first version of this breaker
# matched only the written form and the loop continued straight through it.
RENDERED_ONCE = (
    "_[mailto:jgauf@mozilla.com]_ commented: from Jira, by John Gauf: testing"
)
RENDERED_TWICE = (
    "_\\[mailto:[mailto:jgauf@mozilla.com]\\]_ commented: from Jira, by "
    "John Gauf: testing comment number 2"
)


@pytest.mark.parametrize("body", [RENDERED_ONCE, RENDERED_TWICE])
def test_jira_rendered_markers_are_recognised(body):
    assert was_written_by_forward_sync(body) is True


def test_the_reverse_prefix_anywhere_marks_round_tripped_text():
    """The rendering-independent signal: that phrase exists only because JBI
    wrote it onto a bug, so seeing it in a Jira comment means the text has
    already been round-tripped."""
    assert was_written_by_forward_sync("something from Jira, by Someone: x") is True


@pytest.mark.parametrize(
    "body",
    [
        "I commented: on this yesterday",
        "see the mailto: link in the docs",
        "a normal comment mentioning jira",
    ],
)
def test_human_text_is_not_caught_by_the_broader_matching(body):
    assert was_written_by_forward_sync(body) is False


# --- the forward path's change comments -------------------------------------

# `add_jira_comments_for_changes` posts a JSON blob, not prose, so it carries
# neither prose marker. Copying it back put a duplicate note on the bug for a
# change the bug already records in its own history -- seen live as a status
# change "posting twice".
CHANGE_COMMENT = json.dumps(
    {"modified by": "jgauf@mozilla.com", "resolution": "", "status": "ASSIGNED"},
    indent=4,
)
ASSIGNEE_COMMENT = json.dumps({"assignee": "jgauf@mozilla.com"}, indent=4)


@pytest.mark.parametrize("body", [CHANGE_COMMENT, ASSIGNEE_COMMENT])
def test_change_comments_are_recognised_as_jbis_own(body):
    assert was_written_by_forward_sync(body) is True


@pytest.mark.parametrize(
    "body",
    [
        '{"some": "config", "a user": "pasted this"}',
        '{"status": "ASSIGNED", "extra": "field not ours"}',
        "not json at all",
        '{"broken": ',
    ],
)
def test_other_json_is_not_mistaken_for_a_change_comment(body):
    """Matching is by exact key set, so a user pasting JSON that happens to
    mention `status` still syncs."""
    assert was_written_by_forward_sync(body) is False
