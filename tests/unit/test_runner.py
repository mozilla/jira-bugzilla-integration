"""
Module for testing jbi/runner.py
"""
import logging

# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

import pytest

from jbi import Operation
from jbi.environment import Settings
from jbi.errors import IgnoreInvalidRequestError
from jbi.models import Action, Actions, BugzillaBug, BugzillaWebhookRequest
from jbi.runner import execute_action
from tests.fixtures.factories import bug_factory


def test_request_is_ignored_because_private(
    webhook_create_private_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
    mocked_bugzilla,
):
    bug = bug_factory(id=webhook_create_private_example.bug.id, is_private=True)
    mocked_bugzilla.get_bug.return_value = bug
    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_private_example,
            actions=actions_example,
            settings=settings,
        )

    assert str(exc_info.value) == "private bugs are not valid for action 'devtest'"


def test_private_request_is_allowed(
    webhook_create_private_example: BugzillaWebhookRequest,
    settings: Settings,
    actions_example,
    mocked_bugzilla,
):
    bug = bug_factory(id=webhook_create_private_example.bug.id, is_private=True)
    mocked_bugzilla.get_bug.return_value = bug

    actions_example["devtest"].allow_private = True

    result = execute_action(
        request=webhook_create_private_example,
        actions=actions_example,
        settings=settings,
    )

    bug = BugzillaBug.parse_raw(result["bug"])
    assert bug.id == 654321


def test_request_is_ignored_because_no_action(
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "bar"

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )
    assert str(exc_info.value) == "no action matching bug whiteboard tags: bar"


def test_execution_logging_for_successful_requests(
    caplog,
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    with caplog.at_level(logging.DEBUG):
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )

    captured_log_msgs = [
        r.msg % r.args for r in caplog.records if r.name == "jbi.runner"
    ]

    assert captured_log_msgs == [
        "Handling incoming request",
        "Execute action 'devtest:tests.fixtures.noop_action' for Bug 654321",
        "Action 'devtest' executed successfully for Bug 654321",
    ]


def test_execution_logging_for_ignored_requests(
    caplog,
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "foo"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(IgnoreInvalidRequestError):
            execute_action(
                request=webhook_create_example,
                actions=actions_example,
                settings=settings,
            )

    captured_log_msgs = [
        r.msg % r.args for r in caplog.records if r.name == "jbi.runner"
    ]

    assert captured_log_msgs == [
        "Handling incoming request",
        "Ignore incoming request: no action matching bug whiteboard tags: foo",
    ]


def test_action_is_logged_as_success_if_returns_true(
    caplog,
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    with mock.patch("jbi.models.Action.caller") as mocked:
        mocked.return_value = True, {}

        with caplog.at_level(logging.DEBUG):
            execute_action(
                request=webhook_create_example,
                actions=actions_example,
                settings=settings,
            )

    captured_log_msgs = [
        (r.msg % r.args, r.operation) for r in caplog.records if r.name == "jbi.runner"
    ]

    assert captured_log_msgs == [
        ("Handling incoming request", Operation.HANDLE),
        (
            "Execute action 'devtest:tests.fixtures.noop_action' for Bug 654321",
            Operation.EXECUTE,
        ),
        ("Action 'devtest' executed successfully for Bug 654321", Operation.SUCCESS),
    ]
    assert caplog.records[-1].bug["id"] == 654321
    assert caplog.records[-1].action["whiteboard_tag"] == "devtest"


def test_action_is_logged_as_ignore_if_returns_false(
    caplog,
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    with mock.patch("jbi.models.Action.caller") as mocked:
        mocked.return_value = False, {}

        with caplog.at_level(logging.DEBUG):
            execute_action(
                request=webhook_create_example,
                actions=actions_example,
                settings=settings,
            )

    captured_log_msgs = [
        (r.msg % r.args, r.operation) for r in caplog.records if r.name == "jbi.runner"
    ]

    assert captured_log_msgs == [
        ("Handling incoming request", Operation.HANDLE),
        (
            "Execute action 'devtest:tests.fixtures.noop_action' for Bug 654321",
            Operation.EXECUTE,
        ),
        ("Action 'devtest' executed successfully for Bug 654321", Operation.IGNORE),
    ]


def test_counter_is_incremented_on_ignored_requests(
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    assert webhook_create_example.bug
    webhook_create_example.bug.whiteboard = "foo"

    with mock.patch("jbi.runner.statsd") as mocked:
        with pytest.raises(IgnoreInvalidRequestError):
            execute_action(
                request=webhook_create_example,
                actions=actions_example,
                settings=settings,
            )
    mocked.incr.assert_called_with("jbi.bugzilla.ignored.count")


def test_counter_is_incremented_on_processed_requests(
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    with mock.patch("jbi.runner.statsd") as mocked:
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )
    mocked.incr.assert_called_with("jbi.bugzilla.processed.count")


def test_bugzilla_object_is_fetched_when_private(
    mocked_bugzilla,
    webhook_modify_private_example,
    actions_example: Actions,
    settings: Settings,
):
    fetched_private_bug = bug_factory(
        id=webhook_modify_private_example.bug.id,
        is_private=webhook_modify_private_example.bug.is_private,
        see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    mocked_bugzilla.get_bug.return_value = fetched_private_bug
    actions_example["devtest"].allow_private = True

    # when the runner executes a private bug
    execute_action(
        request=webhook_modify_private_example,
        actions=actions_example,
        settings=settings,
    )

    # then
    mocked_bugzilla.get_bug.assert_called_once_with(
        webhook_modify_private_example.bug.id
    )
