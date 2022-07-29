"""
Module for testing src/jbi/runner.py
"""
import logging

# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

import pytest

from src.app.api import app
from src.app.environment import Settings
from src.jbi import Operation
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.models import Action, Actions
from src.jbi.runner import execute_action


def test_request_is_ignored_because_private(
    caplog,
    webhook_create_private_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_private_example,
            actions=actions_example,
            settings=settings,
        )

    assert str(exc_info.value) == "private bugs are not valid for action 'devtest'"


def test_private_request_is_allowed(
    caplog,
    webhook_create_private_example: BugzillaWebhookRequest,
    settings: Settings,
    actions_example,
):

    actions_example["devtest"].allow_private = True

    result = execute_action(
        request=webhook_create_private_example,
        actions=actions_example,
        settings=settings,
    )

    payload = BugzillaWebhookRequest.parse_raw(result["payload"])
    assert payload.bug
    assert payload.bug.id == 654321


def test_request_is_ignored_because_no_bug(
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    webhook_create_example.bug = None

    with pytest.raises(IgnoreInvalidRequestError) as exc_info:
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )
    assert str(exc_info.value) == "no bug data received"


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
        r.msg % r.args for r in caplog.records if r.name == "src.jbi.runner"
    ]

    assert captured_log_msgs == [
        "Handling incoming request",
        "Execute action 'devtest:tests.unit.jbi.noop_action' for Bug 654321",
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
        r.msg % r.args for r in caplog.records if r.name == "src.jbi.runner"
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
    with mock.patch("src.jbi.models.Action.caller") as mocked:
        mocked.return_value = True, {}
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )
    captured_log_msgs = [
        (r.msg % r.args, r.operation)
        for r in caplog.records
        if r.name == "src.jbi.runner"
    ]

    assert captured_log_msgs == [
        ("Handling incoming request", Operation.HANDLE),
        (
            "Execute action 'devtest:tests.unit.jbi.noop_action' for Bug 654321",
            Operation.EXECUTE,
        ),
        ("Action 'devtest' executed successfully for Bug 654321", Operation.SUCCESS),
    ]


def test_action_is_logged_as_ignore_if_returns_false(
    caplog,
    webhook_create_example: BugzillaWebhookRequest,
    actions_example: Actions,
    settings: Settings,
):
    with mock.patch("src.jbi.models.Action.caller") as mocked:
        mocked.return_value = False, {}
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )

    captured_log_msgs = [
        (r.msg % r.args, r.operation)
        for r in caplog.records
        if r.name == "src.jbi.runner"
    ]

    assert captured_log_msgs == [
        ("Handling incoming request", Operation.HANDLE),
        (
            "Execute action 'devtest:tests.unit.jbi.noop_action' for Bug 654321",
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

    with mock.patch("src.jbi.runner.statsd") as mocked:
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
    with mock.patch("src.jbi.runner.statsd") as mocked:
        execute_action(
            request=webhook_create_example,
            actions=actions_example,
            settings=settings,
        )
    mocked.incr.assert_called_with("jbi.bugzilla.processed.count")
