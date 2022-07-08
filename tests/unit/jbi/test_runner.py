"""
Module for testing src/jbi/runner.py
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

import pytest

from src.app.api import app
from src.app.environment import Settings
from src.jbi.runner import execute_action
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.models import Action, Actions


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
):
    actions = Actions.parse_obj(
        {
            "devtest": {
                "action": "tests.unit.jbi.noop_action",
                "allow_private": True,
                "description": "test config",
                "parameters": {"whiteboard_tag": "devtest"},
            }
        }
    )

    result = execute_action(
        request=webhook_create_private_example,
        actions=actions,
        settings=settings,
    )

    payload = BugzillaWebhookRequest.parse_raw(result["payload"])
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
    webhook_create_example.bug.whiteboard = "foo"

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
