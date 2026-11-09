"""Tests for the publish-time gates.

These run in the web process before an event is put on Pub/Sub, so they may
only look at the payload and the configuration -- never at Jira or Bugzilla.
The assertions that matter are the conservative ones: when an event *might*
be actionable by some action, it must be published, because dropping it here
is unrecoverable.
"""

from unittest import mock

import pytest

from jbi.jira_inbound.preflight import SkipReason, preflight
from jbi.models import Actions


def _actions(action_factory, **overrides):
    params = {
        "parameters__jira_project_key": "JBI",
        "parameters__jira_inbound_enabled": True,
    }
    params.update(overrides)
    return Actions(root=[action_factory(whiteboard_tag="devtest", **params)])


def test_an_actionable_event_has_nothing_to_skip(jira_webhook_event, action_factory):
    assert preflight(jira_webhook_event, _actions(action_factory)) is None


def test_event_without_an_issue_key_is_skipped(
    jira_webhook_request_factory, action_factory
):
    event = jira_webhook_request_factory(issue=None)

    skip = preflight(event, _actions(action_factory))

    assert skip.reason == SkipReason.NO_ISSUE_KEY


def test_event_from_a_project_with_inbound_disabled_is_skipped(
    jira_webhook_event, action_factory
):
    actions = _actions(action_factory, parameters__jira_inbound_enabled=False)

    skip = preflight(jira_webhook_event, actions)

    assert skip.reason == SkipReason.PROJECT_NOT_ENABLED


def test_event_from_an_unconfigured_project_is_skipped(
    jira_webhook_event, action_factory
):
    actions = _actions(action_factory, parameters__jira_project_key="OTHER")

    skip = preflight(jira_webhook_event, actions)

    assert skip.reason == SkipReason.PROJECT_NOT_ENABLED


def test_bot_authored_event_is_skipped(jira_webhook_request_factory, action_factory):
    event = jira_webhook_request_factory(user__accountId="jbi-bot-account-id")

    with mock.patch(
        "jbi.jira_inbound.handler.settings.jira_bot_account_id", "jbi-bot-account-id"
    ):
        skip = preflight(event, _actions(action_factory))

    assert skip.reason == SkipReason.BOT_AUTHORED


def test_event_without_changelog_is_skipped(
    jira_webhook_request_factory, action_factory
):
    event = jira_webhook_request_factory(changelog=None)

    skip = preflight(event, _actions(action_factory))

    assert skip.reason == SkipReason.NO_CHANGELOG


def test_a_comment_event_is_published_without_a_changelog(
    jira_webhook_request_factory, action_factory
):
    """Jira's comment events carry the comment and no changelog; both gates must
    let them through or no comment can ever sync."""
    event = jira_webhook_request_factory(with_comment=True, changelog=None)

    assert preflight(event, _actions(action_factory)) is None


def test_event_without_changelog_is_published_if_any_action_allows_it(
    jira_webhook_request_factory, action_factory
):
    """The publisher cannot tell which action a bug will match (that needs the
    bug's whiteboard), so one permissive action is enough to keep the event."""
    event = jira_webhook_request_factory(changelog=None)
    actions = Actions(
        root=[
            action_factory(
                whiteboard_tag="strict",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            ),
            action_factory(
                whiteboard_tag="lenient",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
                parameters__reverse_sync_without_changelog=True,
            ),
        ]
    )

    assert preflight(event, actions) is None


def test_event_carrying_the_stop_label_is_skipped(
    jira_webhook_request_factory,
    jira_issue_factory,
    jira_issue_fields_factory,
    action_factory,
):
    issue = jira_issue_factory(
        fields=jira_issue_fields_factory(labels=["JBI-Sync-Stop"])
    )
    event = jira_webhook_request_factory(issue=issue)
    actions = _actions(action_factory, parameters__sync_stop_label="jbi-sync-stop")

    skip = preflight(event, actions)

    assert skip.reason == SkipReason.STOP_LABEL


def test_stop_label_is_ignored_when_one_action_does_not_use_it(
    jira_webhook_request_factory,
    jira_issue_factory,
    jira_issue_fields_factory,
    action_factory,
):
    issue = jira_issue_factory(
        fields=jira_issue_fields_factory(labels=["jbi-sync-stop"])
    )
    event = jira_webhook_request_factory(issue=issue)
    actions = Actions(
        root=[
            action_factory(
                whiteboard_tag="stops",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
                parameters__sync_stop_label="jbi-sync-stop",
            ),
            action_factory(
                whiteboard_tag="never-stops",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            ),
        ]
    )

    assert preflight(event, actions) is None


def test_absent_labels_do_not_read_as_a_stop_label(jira_webhook_event, action_factory):
    """A payload without a `labels` key says nothing about labels; the consumer
    fetches them. Publishing is the safe answer."""
    actions = _actions(action_factory, parameters__sync_stop_label="jbi-sync-stop")

    assert preflight(jira_webhook_event, actions) is None


@pytest.mark.parametrize("reason", list(SkipReason))
def test_every_skip_reason_is_a_valid_metric_name_part(reason):
    assert str(reason).replace("_", "").isalnum()
