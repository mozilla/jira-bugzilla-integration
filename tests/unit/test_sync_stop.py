"""Tests for the sync-stop label (a human escape hatch, both directions)."""

import logging

import pytest

import tests.fixtures.factories as factories
from jbi.errors import IgnoreInvalidRequestError
from jbi.jira_inbound.handler import execute_jira_event
from jbi.models import Actions
from jbi.runner import execute_action
from jbi.writeback import sync_is_stopped

STOP = "jbi-sync-stop"


# --- The predicate ----------------------------------------------------------


@pytest.mark.parametrize(
    "labels,expected",
    [
        ([STOP], True),
        (["bugzilla", STOP], True),
        # Jira preserves label case; a user typing it differently means the same.
        (["JBI-Sync-Stop"], True),
        ([" jbi-sync-stop "], True),
        (["bugzilla", "devtest"], False),
        ([], False),
        ([None], False),
    ],
)
def test_stop_label_detection(labels, expected):
    assert sync_is_stopped(labels, STOP) is expected


def test_unconfigured_stop_label_never_stops_anything():
    """Unset means the feature is off; an empty string must not match the
    absence of labels."""
    assert sync_is_stopped([STOP], None) is False
    assert sync_is_stopped([STOP], "") is False


# --- Forward direction: BMO -> Jira ----------------------------------------


@pytest.fixture
def stoppable_actions(action_factory):
    return Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
                parameters__sync_stop_label=STOP,
            )
        ]
    )


def _linked_webhook(webhook_request_factory, settings):
    return webhook_request_factory(
        bug__see_also=[f"{settings.jira_base_url}browse/JBI-234"],
        event__action="modify",
        event__changes=[
            factories.WebhookEventChangeFactory(
                field="summary", removed="old", added="new"
            )
        ],
    )


def test_forward_sync_is_skipped_when_the_issue_is_stopped(
    webhook_request_factory,
    stoppable_actions,
    mocked_jira,
    mocked_bugzilla,
    settings,
    capturelogs,
):
    webhook = _linked_webhook(webhook_request_factory, settings)
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {
        "fields": {"project": {"key": "JBI"}, "labels": ["bugzilla", STOP]}
    }

    with capturelogs.for_logger("jbi.runner").at_level(logging.INFO):
        execute_action(request=webhook, actions=stoppable_actions)

    assert not mocked_jira.update_issue_field.called
    assert not mocked_jira.update_issue.called
    assert any("Sync stopped" in r.message for r in capturelogs.records)


def test_forward_sync_proceeds_without_the_label(
    webhook_request_factory, stoppable_actions, mocked_jira, mocked_bugzilla, settings
):
    webhook = _linked_webhook(webhook_request_factory, settings)
    mocked_bugzilla.get_bug.return_value = webhook.bug
    mocked_jira.get_issue.return_value = {
        "fields": {"project": {"key": "JBI"}, "labels": ["bugzilla"]}
    }

    execute_action(request=webhook, actions=stoppable_actions)

    assert mocked_jira.update_issue_field.called


def test_removing_the_label_resumes_forward_sync(
    webhook_request_factory, stoppable_actions, mocked_jira, mocked_bugzilla, settings
):
    """The label is a pause button, not a one-way door."""
    webhook = _linked_webhook(webhook_request_factory, settings)
    mocked_bugzilla.get_bug.return_value = webhook.bug

    mocked_jira.get_issue.return_value = {
        "fields": {"project": {"key": "JBI"}, "labels": [STOP]}
    }
    execute_action(request=webhook, actions=stoppable_actions)
    assert not mocked_jira.update_issue_field.called

    mocked_jira.get_issue.return_value = {
        "fields": {"project": {"key": "JBI"}, "labels": []}
    }
    execute_action(request=webhook, actions=stoppable_actions)
    assert mocked_jira.update_issue_field.called


def test_creation_is_unaffected(
    webhook_request_factory, stoppable_actions, mocked_jira, mocked_bugzilla
):
    """An unlinked bug has no Jira issue to carry a label, so creation cannot
    be stopped this way."""
    webhook = webhook_request_factory(bug__see_also=[])
    mocked_bugzilla.get_bug.return_value = webhook.bug

    execute_action(request=webhook, actions=stoppable_actions)

    assert mocked_jira.create_issue.called


# --- Reverse direction: Jira -> BMO ----------------------------------------


def test_reverse_sync_is_stopped_by_a_label_in_the_payload(
    jira_webhook_request_factory,
    stoppable_actions,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    event = jira_webhook_request_factory(issue__fields__labels=["bugzilla", STOP])

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_jira_event(event, stoppable_actions)

    assert "sync stopped" in str(exc_info.value)
    assert not mocked_bugzilla.update_bug.called


def test_reverse_sync_fetches_labels_when_the_payload_omits_them(
    jira_webhook_request_factory,
    stoppable_actions,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    """An absent `labels` key is not the same as "no labels" -- the rule may
    simply not send them, and treating that as unlabelled would ignore the
    user's stop request."""
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    mocked_jira.get_issue.return_value = {"fields": {"labels": [STOP]}}
    event = jira_webhook_request_factory(issue__fields__labels=None)

    with pytest.raises(IgnoreInvalidRequestError):
        execute_jira_event(event, stoppable_actions)

    assert mocked_jira.get_issue.called
    assert not mocked_bugzilla.update_bug.called


def test_reverse_sync_proceeds_without_the_label(
    jira_webhook_request_factory,
    stoppable_actions,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        status="NEW",
        resolution="",
        assigned_to="owner@mozilla.com",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    event = jira_webhook_request_factory(issue__fields__labels=["bugzilla"])

    details = execute_jira_event(event, stoppable_actions)

    assert details["steps"]["writeback_status"] == "SUCCESS"


def test_action_without_a_stop_label_configured_is_unaffected(
    jira_webhook_request_factory,
    action_factory,
    mocked_jira,
    mocked_bugzilla,
    bug_factory,
    settings,
):
    """Default-off: an action that has not opted in behaves exactly as before,
    even if someone happens to use that label for their own purposes."""
    actions = Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            )
        ]
    )
    mocked_jira.get_issue_remote_links.return_value = [{"globalId": "654321"}]
    mocked_bugzilla.get_bug.return_value = bug_factory(
        id=654321,
        whiteboard="[devtest]",
        status="NEW",
        resolution="",
        assigned_to="owner@mozilla.com",
        see_also=[f"{settings.jira_base_url}browse/JBI-234"],
    )
    event = jira_webhook_request_factory(issue__fields__labels=[STOP])

    details = execute_jira_event(event, actions)

    assert details["steps"]["writeback_status"] == "SUCCESS"
