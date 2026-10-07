"""Tests for the write-back visibility guard (R-12, plan D10 + hardening)."""

from jbi.jira_inbound.models import (
    JiraComment,
    JiraIssue,
    JiraIssueFields,
    JiraSecurityLevel,
    JiraVisibility,
    JiraWebhookRequest,
)
from jbi.visibility import (
    bug_restriction_reason,
    can_copy_jira_text_to_bug,
    jira_comment_restriction_reason,
    jira_issue_restriction_reason,
)


def test_public_bug_has_no_restriction_reason(bug_factory):
    assert bug_restriction_reason(bug_factory(is_private=False, groups=[])) is None


def test_private_bug_is_restricted(bug_factory):
    assert bug_restriction_reason(bug_factory(is_private=True, groups=[])) is not None


def test_group_restricted_bug_is_restricted(bug_factory):
    """`groups` is how BMO marks security/embargoed bugs."""
    bug = bug_factory(is_private=False, groups=["mozilla-employee-confidential"])

    assert bug_restriction_reason(bug) is not None


def test_bug_with_absent_is_private_but_groups_is_restricted(bug_factory):
    """`is_private` is Optional; an absent value must not read as public."""
    assert bug_restriction_reason(bug_factory(is_private=None, groups=["sec"]))


def _event(**kw):
    return JiraWebhookRequest(**kw)


def test_unrestricted_comment_has_no_reason():
    event = _event(comment=JiraComment(body="hello"))

    assert jira_comment_restriction_reason(event) is None


def test_role_restricted_comment_is_flagged():
    event = _event(
        comment=JiraComment(
            body="hello", visibility=JiraVisibility(type="role", value="Developers")
        )
    )

    assert "Developers" in jira_comment_restriction_reason(event)


def test_internal_only_comment_is_flagged():
    event = _event(comment=JiraComment(body="x", jsdPublic=False))

    assert "internal-only" in jira_comment_restriction_reason(event)


def test_public_jsd_comment_is_not_flagged():
    event = _event(comment=JiraComment(body="x", jsdPublic=True))

    assert jira_comment_restriction_reason(event) is None


def test_issue_security_level_is_flagged():
    event = _event(
        issue=JiraIssue(
            key="X-1",
            fields=JiraIssueFields(security=JiraSecurityLevel(name="Embargo")),
        )
    )

    assert "Embargo" in jira_issue_restriction_reason(event)


def test_issue_without_security_level_is_not_flagged():
    event = _event(issue=JiraIssue(key="X-1", fields=JiraIssueFields(summary="hi")))

    assert jira_issue_restriction_reason(event) is None


def test_copy_guard_checks_both_ends(bug_factory):
    """Either end can veto: the bug's audience, or the Jira content's own
    classification."""
    public_bug = bug_factory(is_private=False, groups=[])
    restricted_bug = bug_factory(is_private=False, groups=["core-security"])
    clean = _event(
        issue=JiraIssue(key="X-1", fields=JiraIssueFields(summary="hi")),
        comment=JiraComment(body="hi"),
    )
    embargoed = _event(
        issue=JiraIssue(
            key="X-1", fields=JiraIssueFields(security=JiraSecurityLevel(name="Emb"))
        ),
        comment=JiraComment(body="hi"),
    )

    assert can_copy_jira_text_to_bug(public_bug, clean) is None
    assert can_copy_jira_text_to_bug(restricted_bug, clean) is not None
    assert can_copy_jira_text_to_bug(public_bug, embargoed) is not None


def test_bug_restriction_reason_names_the_groups(bug_factory):
    reason = bug_restriction_reason(bug_factory(groups=["core-security", "embargo"]))

    assert "core-security" in reason and "embargo" in reason


# --- internal vs jsdPublic are inverses ------------------------------------


def test_internal_true_is_restricted():
    """Automation's `{{comment.internal}}` renders `true` for an
    internal-only comment."""
    event = _event(comment=JiraComment(body="x", internal=True))

    assert "internal=true" in jira_comment_restriction_reason(event)


def test_internal_false_is_not_restricted():
    """The bug this pins: mapping `internal` onto `jsdPublic` inverted the
    meaning, so a public comment (`internal: false`) read as internal-only
    and every comment was blocked."""
    event = _event(comment=JiraComment(body="x", internal=False))

    assert jira_comment_restriction_reason(event) is None


def test_jsd_public_false_is_still_restricted():
    """Jira's own webhooks use the opposite spelling."""
    event = _event(comment=JiraComment(body="x", jsdPublic=False))

    assert "jsdPublic=false" in jira_comment_restriction_reason(event)


def test_neither_flag_present_is_not_restricted():
    event = _event(comment=JiraComment(body="x"))

    assert jira_comment_restriction_reason(event) is None
