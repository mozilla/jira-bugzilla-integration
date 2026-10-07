"""Tests for the reverse (Jira -> BMO) field writers, plan D9.

The interesting cases are the ones section 4.1 of the plan argues about: that
`status_map` is never inverted, that a resolution is never guessed, and that
a value which does not round-trip cannot corrupt the bug.
"""

from unittest import mock

import pytest

from jbi import jira_steps
from jbi.identity import UNASSIGNED_EMAIL, IdentityEntry, IdentityMap
from jbi.jira_inbound.models import (
    JiraNamedValue,
    JiraSecurityLevel,
    JiraVisibility,
)
from jbi.jira_steps import ReverseContext, ReverseStepStatus


@pytest.fixture
def mocked_service(mocked_bugzilla):
    from jbi import bugzilla

    return bugzilla.service.BugzillaService(mocked_bugzilla)


def make_context(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    *,
    bug_kwargs=None,
    action_kwargs=None,
    **event_kwargs,
):
    action = action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__jira_inbound_enabled=True,
        **(action_kwargs or {}),
    )
    # Default the bug to *assigned*: BMO refuses `status: ASSIGNED` on an
    # unassigned bug, so an unassigned default would make most status tests
    # exercise that edge case rather than the mapping they are about. Tests
    # that care pass `assigned_to` explicitly.
    bug_kwargs = {"assigned_to": "owner@mozilla.com", **(bug_kwargs or {})}
    return ReverseContext(
        action=action,
        bug=bug_factory(**bug_kwargs),
        issue_key="JBI-234",
        event=jira_webhook_request_factory(**event_kwargs),
    )


# --- Status & resolution (plan section 4.1) --------------------------------


@pytest.mark.parametrize(
    "category,bug_status,expected",
    [
        ("indeterminate", "NEW", "ASSIGNED"),
        ("done", "ASSIGNED", "RESOLVED"),
        # A bug coming back out of a resolved state is REOPENED, not NEW:
        # writing NEW would erase the fact that it was ever closed.
        ("new", "RESOLVED", "REOPENED"),
        ("new", "VERIFIED", "REOPENED"),
    ],
)
def test_status_category_drives_the_reverse_status(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    category,
    bug_status,
    expected,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": bug_status, "resolution": ""},
        issue__fields__status__statusCategory__key=category,
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    written = mocked_bugzilla.update_bug.call_args.kwargs
    assert written["status"] == expected


def test_reverse_status_is_not_derived_from_status_map(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """The review finding this design answers: a many-to-one `status_map`
    cannot be inverted, so it must not participate in the reverse direction."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        # A realistic prod-shaped map: ten BMO values -> one Jira status.
        action_kwargs={
            "parameters__status_map": {
                "RESOLVED": "Done",
                "VERIFIED": "Done",
                "FIXED": "Done",
                "WONTFIX": "Done",
                "DUPLICATE": "Done",
            }
        },
        bug_kwargs={"status": "ASSIGNED", "resolution": ""},
        issue__fields__status__name="Done",
        issue__fields__status__statusCategory__key="done",
    )

    jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    # RESOLVED comes from the status *category*, not from reversing the map
    # (which could equally have produced WONTFIX or DUPLICATE).
    assert mocked_bugzilla.update_bug.call_args.kwargs["status"] == "RESOLVED"


def test_resolution_comes_from_the_inverted_resolution_map(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={
            "parameters__resolution_map": {
                "FIXED": "Done",
                "WONTFIX": "Won't Do",
                "DUPLICATE": "Duplicate",
            }
        },
        bug_kwargs={"status": "ASSIGNED", "resolution": ""},
        issue__fields__status__statusCategory__key="done",
        issue__fields__resolution=JiraNamedValue(name="Won't Do"),
    )

    jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    written = mocked_bugzilla.update_bug.call_args.kwargs
    assert written == {"status": "RESOLVED", "resolution": "WONTFIX"}


def test_default_resolution_is_used_when_jira_has_none(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__default_reverse_resolution": "FIXED"},
        bug_kwargs={"status": "ASSIGNED", "resolution": ""},
        issue__fields__status__statusCategory__key="done",
        issue__fields__resolution=None,
    )

    jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert mocked_bugzilla.update_bug.call_args.kwargs["resolution"] == "FIXED"


def test_resolution_is_left_alone_when_it_cannot_be_determined(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    capturelogs,
):
    """Never guess a resolution: a wrong DUPLICATE or WONTFIX is a false claim
    about the bug that misleads every later reader."""
    import logging

    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "ASSIGNED", "resolution": ""},
        issue__fields__status__statusCategory__key="done",
        issue__fields__resolution=None,
    )

    with capturelogs.for_logger("jbi.jira_steps").at_level(logging.WARNING):
        jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    written = mocked_bugzilla.update_bug.call_args.kwargs
    assert written == {"status": "RESOLVED"}
    assert any("no BMO resolution" in r.message for r in capturelogs.records)


def test_reopening_clears_a_stale_resolution(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """An open bug that still claims to be FIXED is worse than either state."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "RESOLVED", "resolution": "FIXED"},
        issue__fields__status__statusCategory__key="indeterminate",
    )

    jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    written = mocked_bugzilla.update_bug.call_args.kwargs
    assert written == {"status": "ASSIGNED", "resolution": ""}


def test_status_override_wins_over_the_default_category_map(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={
            "parameters__reverse_status_overrides": {"indeterminate": "NEW"}
        },
        bug_kwargs={"status": "ASSIGNED"},
        issue__fields__status__statusCategory__key="indeterminate",
    )

    jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert mocked_bugzilla.update_bug.call_args.kwargs["status"] == "NEW"


def test_status_writeback_is_skipped_when_status_did_not_change(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    """A Jira edit of one field must not overwrite BMO fields a human just
    changed by hand."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field="description")],
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


def test_unchanged_status_issues_no_request(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """Invariant C backstop: an echoed value terminates instead of looping."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "ASSIGNED", "resolution": ""},
        issue__fields__status__statusCategory__key="indeterminate",
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


# --- Priority ---------------------------------------------------------------


def test_priority_is_written_through_the_inverted_priority_map(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"priority": "P3"},
        changelog__items=[jira_changelog_item_factory(field="priority")],
        issue__fields__priority=JiraNamedValue(name="P1"),
    )

    status, _ = jira_steps.writeback_priority(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    mocked_bugzilla.update_bug.assert_called_once_with(context.bug.id, priority="P1")


def test_unmapped_priority_writes_nothing(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field="priority")],
        issue__fields__priority=JiraNamedValue(name="Blocker"),
    )

    status, _ = jira_steps.writeback_priority(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


# --- Assignee ---------------------------------------------------------------


def test_assignee_resolves_through_the_identity_map(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
    jira_user_factory,
):
    identity_map = IdentityMap(
        users=[
            IdentityEntry(
                bmo_email="mismatch@mozilla.com", jira_account_id="account-id-x"
            )
        ]
    )
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field="assignee")],
        issue__fields__assignee=jira_user_factory(
            accountId="account-id-x", emailAddress=None
        ),
    )

    with mock.patch("jbi.jira_steps.get_identity_map", return_value=identity_map):
        status, _ = jira_steps.writeback_assignee(
            context, bugzilla_service=mocked_service
        )

    assert status == ReverseStepStatus.SUCCESS
    mocked_bugzilla.update_bug.assert_called_once_with(
        context.bug.id, assigned_to="mismatch@mozilla.com"
    )


def test_assignee_falls_back_to_the_jira_email(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
    jira_user_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field="assignee")],
        issue__fields__assignee=jira_user_factory(
            accountId="account-id-y", emailAddress="person@mozilla.com"
        ),
    )

    with mock.patch("jbi.jira_steps.get_identity_map", return_value=IdentityMap()):
        jira_steps.writeback_assignee(context, bugzilla_service=mocked_service)

    mocked_bugzilla.update_bug.assert_called_once_with(
        context.bug.id, assigned_to="person@mozilla.com"
    )


def test_unresolvable_assignee_leaves_the_bug_alone(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
    jira_user_factory,
):
    """Hidden email and no override: never guess who this is."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field="assignee")],
        issue__fields__assignee=jira_user_factory(
            accountId="account-id-z", emailAddress=None
        ),
    )

    with mock.patch("jbi.jira_steps.get_identity_map", return_value=IdentityMap()):
        status, _ = jira_steps.writeback_assignee(
            context, bugzilla_service=mocked_service
        )

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_unassigning_in_jira_uses_the_bmo_sentinel(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"assigned_to": "person@mozilla.com"},
        changelog__items=[jira_changelog_item_factory(field="assignee")],
        issue__fields__assignee=None,
    )

    jira_steps.writeback_assignee(context, bugzilla_service=mocked_service)

    mocked_bugzilla.update_bug.assert_called_once_with(
        context.bug.id, assigned_to=UNASSIGNED_EMAIL
    )


# --- Summary ----------------------------------------------------------------


def test_summary_is_written_back(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"summary": "Old title"},
        changelog__items=[
            jira_changelog_item_factory(
                field="summary", fromString="Old title", toString="New title"
            )
        ],
        issue__fields__summary="New title",
    )

    status, _ = jira_steps.writeback_summary(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    mocked_bugzilla.update_bug.assert_called_once_with(
        context.bug.id, summary="New title"
    )


def test_summary_writeback_skipped_when_unchanged_in_jira(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field="status")],
        issue__fields__summary="New title",
    )

    status, _ = jira_steps.writeback_summary(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


# --- Comment write-back + visibility (R-12, plan D10) ----------------------


def test_comment_is_copied_with_attribution(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """Attribution is required by the PRD and makes a copied comment
    recognisable rather than looking like the service account's own words."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="Looks good to me.",
        comment__author__displayName="Jane Reviewer",
    )
    mocked_bugzilla.get_comments.return_value = []

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    written = mocked_bugzilla.update_bug.call_args.kwargs["comment"]["body"]
    assert written == "from Jira, by Jane Reviewer:\nLooks good to me."


def test_comment_attribution_prefers_the_identity_map(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_user_factory,
):
    """The map is the only source that can name someone whose Jira profile
    hides their identity details."""
    identity_map = IdentityMap(
        users=[
            IdentityEntry(
                bmo_email="hidden@mozilla.com",
                jira_account_id="account-id-hidden",
                display_name="Real Name",
            )
        ]
    )
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="hi",
        comment__author=jira_user_factory(
            accountId="account-id-hidden", displayName="hidden user"
        ),
    )
    mocked_bugzilla.get_comments.return_value = []

    with mock.patch("jbi.jira_steps.get_identity_map", return_value=identity_map):
        jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    written = mocked_bugzilla.update_bug.call_args.kwargs["comment"]["body"]
    assert written.startswith("from Jira, by Real Name:")


def test_comment_is_not_written_to_a_restricted_bug(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """Internal planning context must never land on a bug whose audience JBI
    cannot reason about."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"groups": ["core-security"]},
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="internal discussion",
    )

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_long_comment_is_truncated_rather_than_dropped(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="x" * 100_000,
    )
    mocked_bugzilla.get_comments.return_value = []

    jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    written = mocked_bugzilla.update_bug.call_args.kwargs["comment"]["body"]
    assert len(written) <= jira_steps.BMO_COMMENT_MAX_LENGTH
    assert written.endswith(jira_steps.TRUNCATION_MARKER)


def test_event_without_a_comment_writes_nothing(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = make_context(action_factory, bug_factory, jira_webhook_request_factory)

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


def test_duplicate_comment_is_not_posted_twice(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    comment_factory,
):
    """A redelivered event must not append the same text again."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="Looks good to me.",
        comment__author__displayName="Jane Reviewer",
    )
    mocked_bugzilla.get_comments.return_value = [
        comment_factory(text="from Jira, by Jane Reviewer:\nLooks good to me.")
    ]

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


# --- Field ownership & conflict policy (plan section 4, D11) ---------------


def test_summary_conflict_resolves_to_the_bmo_value(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
    capturelogs,
):
    """Both sides edited before sync reconciled: BMO is authoritative for
    execution fields, so the Jira value must not clobber it."""
    import logging

    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        # BMO now says "Edited in BMO", but Jira thinks the previous value was
        # still "Old title" -- so BMO moved independently.
        bug_kwargs={"summary": "Edited in BMO"},
        changelog__items=[
            jira_changelog_item_factory(
                field="summary", fromString="Old title", toString="Edited in Jira"
            )
        ],
        issue__fields__summary="Edited in Jira",
    )

    with capturelogs.for_logger("jbi.jira_steps").at_level(logging.INFO):
        status, _ = jira_steps.writeback_summary(
            context, bugzilla_service=mocked_service
        )

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called
    assert any("BMO wins" in record.message for record in capturelogs.records)


def test_priority_conflict_resolves_to_the_bmo_value(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"priority": "P1"},
        changelog__items=[
            jira_changelog_item_factory(
                field="priority", fromString="P3", toString="P2"
            )
        ],
        issue__fields__priority=JiraNamedValue(name="P2"),
    )

    status, _ = jira_steps.writeback_priority(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


def test_write_proceeds_when_no_previous_value_is_known(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    """Conflict detection needs Jira's previous value; without it, syncing
    must continue rather than silently stop."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"summary": "Old title"},
        changelog__items=[
            jira_changelog_item_factory(field="summary", fromString=None)
        ],
        issue__fields__summary="New title",
    )

    status, _ = jira_steps.writeback_summary(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS


@pytest.mark.parametrize(
    "field", ["Sprint", "Story Points", "Epic Link", "parent", "labels", "Rank"]
)
def test_planning_field_changes_write_nothing(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
    field,
):
    """R-07: re-parenting or re-planning in Jira never touches BMO."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[jira_changelog_item_factory(field=field)],
    )

    from jbi.jira_steps import ReverseExecutor

    ReverseExecutor(bugzilla_service=mocked_service)(context)

    assert not mocked_bugzilla.update_bug.called


def test_suppressed_planning_fields_are_logged(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    capturelogs,
    jira_changelog_item_factory,
):
    import logging

    from jbi.jira_steps import ReverseExecutor

    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        changelog__items=[
            jira_changelog_item_factory(field="Sprint"),
            jira_changelog_item_factory(field="summary"),
        ],
    )

    with capturelogs.for_logger("jbi.jira_steps").at_level(logging.INFO):
        ReverseExecutor(bugzilla_service=mocked_service)(context)

    assert any("Jira-owned fields" in record.message for record in capturelogs.records)


# --- Jira-side confidentiality (R-12 hardening) -----------------------------


def _commented(action_factory, bug_factory, factory, **kw):
    return make_context(
        action_factory,
        bug_factory,
        factory,
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="embargoed: the exploit works like this",
        **kw,
    )


def test_comment_sync_is_off_by_default(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """Copying free text to a world-readable bug requires an explicit opt-in,
    separate from field sync."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        with_comment=True,
        comment__body="internal chatter",
    )

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called


def test_role_restricted_jira_comment_is_not_copied(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = _commented(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        comment__visibility=JiraVisibility(type="role", value="Administrators"),
    )

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_internal_only_jsd_comment_is_not_copied(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    context = _commented(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        comment__jsdPublic=False,
    )

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_comment_on_an_embargoed_issue_is_not_copied(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """An issue security level is Jira's embargo marker."""
    context = _commented(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        issue__fields__security=JiraSecurityLevel(name="Security Team Only"),
    )

    status, _ = jira_steps.writeback_comment(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_summary_of_an_embargoed_issue_is_not_copied(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_changelog_item_factory,
):
    """The title of an embargoed issue can itself describe an unpublished
    vulnerability, so the guard covers summary too -- not only comments."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"summary": "Old title"},
        changelog__items=[
            jira_changelog_item_factory(
                field="summary", fromString="Old title", toString="CVE-2026-1 RCE"
            )
        ],
        issue__fields__summary="CVE-2026-1 RCE in the parser",
        issue__fields__security=JiraSecurityLevel(name="Embargoed"),
    )

    status, _ = jira_steps.writeback_summary(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_status_still_syncs_for_an_embargoed_issue(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """The guard is scoped to free text. A status value carries no embargoed
    detail, and blocking it would silently strand the bug's state."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "NEW", "resolution": ""},
        issue__fields__status__statusCategory__key="indeterminate",
        issue__fields__security=JiraSecurityLevel(name="Embargoed"),
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    assert mocked_bugzilla.update_bug.call_args.kwargs["status"] == "ASSIGNED"


@pytest.mark.parametrize("bug_status", ["NEW", "ASSIGNED", "REOPENED"])
def test_new_category_never_regresses_an_open_bug(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    capturelogs,
    bug_status,
):
    """The pilot project puts **Blocked** in the `new` category, so moving an
    issue from In Progress to Blocked would otherwise write NEW over
    ASSIGNED and regress the bug to "never worked on". Reverse status only
    moves a bug forward; BMO does not model "blocked" as a status anyway."""
    import logging

    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": bug_status, "resolution": ""},
        issue__fields__status__name="Blocked",
        issue__fields__status__statusCategory__key="new",
    )

    with capturelogs.for_logger("jbi.jira_steps").at_level(logging.INFO):
        status, _ = jira_steps.writeback_status(
            context, bugzilla_service=mocked_service
        )

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called
    assert any("regressing" in record.message for record in capturelogs.records)


def test_new_category_still_reopens_a_resolved_bug(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """The one `new`-category transition worth writing: a genuine reopen."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "RESOLVED", "resolution": "FIXED"},
        issue__fields__status__name="To Do",
        issue__fields__status__statusCategory__key="new",
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    written = mocked_bugzilla.update_bug.call_args.kwargs
    assert written == {"status": "REOPENED", "resolution": ""}


def test_missing_status_category_is_incomplete_not_a_silent_noop(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """ "Rule does not send statusCategory" and "Jira moved backwards" are
    different situations and must not report the same way."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        issue__fields__status=None,
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called


def test_category_override_can_still_force_a_new_category_write(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
):
    """An explicit override outranks the never-regress rule, for a project
    whose `new`-category statuses really do mean "not started"."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__reverse_status_overrides": {"new": "NEW"}},
        bug_kwargs={"status": "ASSIGNED", "resolution": ""},
        issue__fields__status__statusCategory__key="new",
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    assert mocked_bugzilla.update_bug.call_args.kwargs["status"] == "NEW"


# --- BMO's ASSIGNED precondition (found against the dev instance) -----------


def test_assigned_status_carries_the_assignee_for_an_unassigned_bug(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_user_factory,
):
    """BMO rejects `status: ASSIGNED` on a bug with no assignee ("you cannot
    set this bug's status to ASSIGNED because the bug is not assigned to a
    person"), so the assignee must travel in the same write."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "NEW", "resolution": "", "assigned_to": UNASSIGNED_EMAIL},
        issue__fields__status__statusCategory__key="indeterminate",
        issue__fields__assignee=jira_user_factory(
            accountId="acc-1", emailAddress="person@mozilla.com"
        ),
    )

    status, _ = jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    assert status == ReverseStepStatus.SUCCESS
    mocked_bugzilla.update_bug.assert_called_once_with(
        context.bug.id, status="ASSIGNED", assigned_to="person@mozilla.com"
    )


def test_assigned_status_is_skipped_when_nobody_can_be_resolved(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    capturelogs,
):
    """BMO cannot represent "in progress but unassigned", and inventing an
    assignee would be worse than leaving the status alone."""
    import logging

    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={"status": "NEW", "resolution": "", "assigned_to": UNASSIGNED_EMAIL},
        issue__fields__status__statusCategory__key="indeterminate",
        issue__fields__assignee=None,
    )

    with capturelogs.for_logger("jbi.jira_steps").at_level(logging.WARNING):
        status, _ = jira_steps.writeback_status(
            context, bugzilla_service=mocked_service
        )

    assert status == ReverseStepStatus.INCOMPLETE
    assert not mocked_bugzilla.update_bug.called
    assert any("unassigned" in r.message for r in capturelogs.records)


def test_assigned_status_leaves_an_existing_assignee_alone(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    jira_user_factory,
):
    """The assignee only rides along when BMO requires it; it must not
    silently reassign a bug that already has an owner."""
    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        bug_kwargs={
            "status": "NEW",
            "resolution": "",
            "assigned_to": "owner@mozilla.com",
        },
        issue__fields__status__statusCategory__key="indeterminate",
        issue__fields__assignee=jira_user_factory(
            accountId="acc-2", emailAddress="someone-else@mozilla.com"
        ),
    )

    jira_steps.writeback_status(context, bugzilla_service=mocked_service)

    mocked_bugzilla.update_bug.assert_called_once_with(
        context.bug.id, status="ASSIGNED"
    )


# --- A rejected write must not discard the rest of the pipeline -------------


def _raising_step(status_code):
    import requests

    def step(context, *, bugzilla_service):
        response = mock.MagicMock(status_code=status_code)
        raise requests.HTTPError("nope", response=response)

    step.__name__ = f"raising_{status_code}"
    return step


def test_client_error_fails_one_step_and_continues(
    action_factory, bug_factory, jira_webhook_request_factory, mocked_service
):
    """A 4xx means BMO rejected *that* write as invalid; retrying will not
    help, and a rejected status must still let the comment through."""
    recorded = []

    def recording_step(context, *, bugzilla_service):
        recorded.append(context.issue_key)
        return (ReverseStepStatus.SUCCESS, context)

    recording_step.__name__ = "recording_step"
    context = make_context(action_factory, bug_factory, jira_webhook_request_factory)

    with mock.patch.object(
        jira_steps, "REVERSE_STEPS", [_raising_step(400), recording_step]
    ):
        details = jira_steps.ReverseExecutor(bugzilla_service=mocked_service)(context)

    assert details["steps"]["raising_400"] == "INCOMPLETE"
    assert details["steps"]["recording_step"] == "SUCCESS"
    assert recorded == ["JBI-234"]


def test_server_error_is_not_swallowed(
    action_factory, bug_factory, jira_webhook_request_factory, mocked_service
):
    """A 5xx is an outage, not an invalid write: the caller must see it."""
    import requests

    context = make_context(action_factory, bug_factory, jira_webhook_request_factory)

    with mock.patch.object(jira_steps, "REVERSE_STEPS", [_raising_step(503)]):
        with pytest.raises(requests.HTTPError):
            jira_steps.ReverseExecutor(bugzilla_service=mocked_service)(context)


def test_reverse_sync_skips_a_comment_jbi_put_on_the_issue(
    action_factory,
    bug_factory,
    jira_webhook_request_factory,
    mocked_service,
    mocked_bugzilla,
    capturelogs,
):
    """Jira side of the loop breaker. JBI's forward sync wrote this comment
    onto the issue from a bug comment; copying it back nests the attribution
    and grows the text every hop."""
    import logging

    context = make_context(
        action_factory,
        bug_factory,
        jira_webhook_request_factory,
        action_kwargs={"parameters__reverse_comment_sync_enabled": True},
        with_comment=True,
        comment__body="*jgauf@mozilla.com* commented: \nfrom Jira, by John Gauf: hi",
    )

    with capturelogs.for_logger("jbi.jira_steps").at_level(logging.INFO):
        status, _ = jira_steps.writeback_comment(
            context, bugzilla_service=mocked_service
        )

    assert status == ReverseStepStatus.NOOP
    assert not mocked_bugzilla.update_bug.called
    assert any("forward sync" in r.message for r in capturelogs.records)
