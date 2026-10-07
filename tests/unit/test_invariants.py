"""Permanent regression guards for the plan's invariants (D12).

These are deliberately in their own module rather than scattered through the
suites of the features that implement them: they are properties of the whole
system, and a future change that breaks one should fail a test whose name
says which invariant it broke.
"""

from unittest import mock

import pytest

import tests.fixtures.factories as factories
from jbi.errors import IgnoreInvalidRequestError
from jbi.jira_inbound.handler import execute_jira_event
from jbi.models import Actions
from jbi.runner import execute_action


@pytest.fixture
def inbound_action(action_factory):
    return action_factory(
        whiteboard_tag="devtest",
        parameters__jira_project_key="JBI",
        parameters__jira_inbound_enabled=True,
    )


# --- Invariant A: never create a duplicate Jira issue ----------------------


def test_invariant_a_linked_bug_is_updated_not_recreated(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    """A bug that already links to an issue must never produce a second one.

    This is the rollout failure mode: the pilot component is full of bugs
    that were linked under the current system, and a naive "create on sync"
    would flood Jira with duplicates of work that already exists.
    """
    webhook = webhook_request_factory(
        bug__see_also=[f"{settings.jira_base_url}browse/JBI-234"],
        event__action="modify",
        event__changes=[
            factories.WebhookEventChangeFactory(
                field="summary", removed="old", added="new"
            )
        ],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}

    execute_action(request=webhook, actions=actions)

    assert not mocked_jira.create_issue.called


def test_invariant_a_holds_when_the_tag_is_re_added(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    """Re-adding the whiteboard tag to an already-linked bug re-syncs every
    field (operation CREATE), which must still not create an issue."""
    webhook = webhook_request_factory(
        bug__see_also=[f"{settings.jira_base_url}browse/JBI-234"],
        event__action="modify",
        event__changes=[
            factories.WebhookEventChangeFactory(
                field="whiteboard", removed="", added="[devtest]"
            )
        ],
    )
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}

    execute_action(request=webhook, actions=actions)

    assert not mocked_jira.create_issue.called


# --- Invariant B: Jira -> BMO never creates a bug --------------------------


def test_invariant_b_uncorrelated_issue_creates_no_bug(
    jira_webhook_event, inbound_action, mocked_jira, mocked_bugzilla
):
    """A standalone Jira issue -- cloud work that never had a bug -- has no
    reverse effect at all."""
    mocked_jira.get_issue_remote_links.return_value = []

    with pytest.raises(IgnoreInvalidRequestError):
        execute_jira_event(jira_webhook_event, Actions(root=[inbound_action]))

    assert not mocked_bugzilla.update_bug.called
    # There is no bug-creation call to make: the client has no such method,
    # so the meaningful assertion is that nothing was written at all.
    assert not mocked_bugzilla.method_calls or all(
        call[0] != "update_bug" for call in mocked_bugzilla.method_calls
    )


# --- Invariant C: no echo in either direction ------------------------------


def test_invariant_c_jira_side_bot_event_is_dropped(
    jira_webhook_request_factory, inbound_action, mocked_jira, mocked_bugzilla
):
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    event = jira_webhook_request_factory(user__accountId="jbi-bot")

    with mock.patch("jbi.jira_inbound.handler.settings.jira_bot_account_id", "jbi-bot"):
        with pytest.raises(IgnoreInvalidRequestError):
            execute_jira_event(event, Actions(root=[inbound_action]))

    assert not mocked_bugzilla.update_bug.called


def test_invariant_c_bugzilla_side_bot_event_is_dropped(
    webhook_request_factory, actions, mocked_jira, mocked_bugzilla, settings
):
    webhook = webhook_request_factory(event__user__login="jbi-bot@mozilla.bugs")
    mocked_bugzilla.get_bug.return_value = webhook.bug

    with mock.patch.object(settings, "bugzilla_bot_login", "jbi-bot@mozilla.bugs"):
        with mock.patch("jbi.runner.settings", settings):
            with pytest.raises(IgnoreInvalidRequestError):
                execute_action(request=webhook, actions=actions)

    assert not mocked_jira.create_issue.called
    assert not mocked_jira.update_issue_field.called


def test_invariant_c_round_trip_terminates(
    jira_webhook_request_factory,
    inbound_action,
    bug_factory,
    mocked_jira,
    mocked_bugzilla,
    settings,
    jira_changelog_item_factory,
):
    """The full loop, in one test: a Jira status change writes BMO exactly
    once, and the Bugzilla webhook that write produces reaches Jira zero
    times.

    This is the property the two echo gates and read-before-write exist for,
    and the one a future refactor is most likely to break silently.
    """
    linked_bug = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        status="NEW",
        resolution="",
        # Assigned, because BMO rejects `status: ASSIGNED` on an unassigned
        # bug -- otherwise leg 1 would write nothing and the loop test would
        # pass for the wrong reason.
        assigned_to="owner@mozilla.com",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = linked_bug

    event = jira_webhook_request_factory(
        changelog__items=[jira_changelog_item_factory(field="status")],
        issue__fields__status__statusCategory__key="indeterminate",
    )

    # Leg 1: Jira -> BMO. Exactly one write.
    execute_jira_event(event, Actions(root=[inbound_action]))

    assert mocked_bugzilla.update_bug.call_count == 1
    assert mocked_bugzilla.update_bug.call_args.kwargs["status"] == "ASSIGNED"

    # Leg 2: the BMO write fires the normal Bugzilla webhook, authored by
    # JBI's own account. It must not reach Jira.
    echo = factories.WebhookRequestFactory(
        bug=linked_bug.model_copy(update={"status": "ASSIGNED"}),
        event=factories.WebhookEventFactory(
            action="modify",
            user=factories.WebhookUserFactory(login="jbi-bot@mozilla.bugs"),
            changes=[
                factories.WebhookEventChangeFactory(
                    field="status", removed="NEW", added="ASSIGNED"
                )
            ],
        ),
    )
    mocked_jira.reset_mock()
    # Make the forward path fully viable, so the echo gate is the *only*
    # thing that can stop leg 2. Without this the linked-project check
    # rejects the event first and the test would pass even with the gate
    # deleted -- it would assert nothing.
    mocked_jira.get_issue.return_value = {"fields": {"project": {"key": "JBI"}}}

    with mock.patch.object(settings, "bugzilla_bot_login", "jbi-bot@mozilla.bugs"):
        with mock.patch("jbi.runner.settings", settings):
            with pytest.raises(IgnoreInvalidRequestError) as exc_info:
                execute_action(request=echo, actions=Actions(root=[inbound_action]))

    assert "authored by JBI itself" in str(exc_info.value)

    assert not mocked_jira.set_issue_status.called
    assert not mocked_jira.update_issue_field.called


def test_invariant_c_backstop_holds_without_the_actor_check(
    jira_webhook_request_factory,
    inbound_action,
    bug_factory,
    mocked_jira,
    mocked_bugzilla,
    settings,
    jira_changelog_item_factory,
):
    """With no bot login configured (or an actor-less event), the echo gate
    cannot fire -- and read-before-write must still stop the loop: the BMO
    value already matches, so no request is sent."""
    linked_bug = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        status="ASSIGNED",
        resolution="",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = linked_bug

    event = jira_webhook_request_factory(
        changelog__items=[jira_changelog_item_factory(field="status")],
        issue__fields__status__statusCategory__key="indeterminate",
    )

    execute_jira_event(event, Actions(root=[inbound_action]))

    assert not mocked_bugzilla.update_bug.called
