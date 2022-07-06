"""
Module for testing src/jbi/router.py
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.app.api import app
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.models import Action


def test_request_is_ignored_because_private(
    caplog, webhook_request_example: BugzillaWebhookRequest
):
    private_webhook_request_example = webhook_request_example
    private_webhook_request_example.bug.is_private = True  # type: ignore
    test_action = Action.parse_obj({"action": "tests.unit.jbi.noop_action"})

    with mock.patch("src.jbi.router.extract_current_action") as mocked_extract_action:
        mocked_extract_action.return_value = "test", test_action
        with mock.patch("src.jbi.router.getbug_as_bugzilla_object") as mocked_bz_func:
            mocked_bz_func.return_value = private_webhook_request_example.bug
            with TestClient(app) as anon_client:
                # https://fastapi.tiangolo.com/advanced/testing-events/

                response = anon_client.post(
                    "/bugzilla_webhook", data=private_webhook_request_example.json()
                )
                assert response
                assert response.status_code == 200
                assert (
                    response.json()["error"]
                    == "private bugs are not valid for action 'test'"
                )

                captured_log_msgs = [
                    r.msg % r.args for r in caplog.records if r.name == "src.jbi.router"
                ]

                assert captured_log_msgs == [
                    "Handling incoming request",
                    "Ignore incoming request: private bugs are not valid for action 'test'",
                ]


def test_private_request_is_allowed(
    caplog, webhook_request_example: BugzillaWebhookRequest
):
    private_webhook_request_example = webhook_request_example
    private_webhook_request_example.bug.is_private = True  # type: ignore
    test_action = Action.parse_obj(
        {"action": "tests.unit.jbi.noop_action", "allow_private": True}
    )

    with mock.patch("src.jbi.router.extract_current_action") as mocked_extract_action:
        mocked_extract_action.return_value = "test", test_action
        with mock.patch("src.jbi.router.getbug_as_bugzilla_object") as mocked_bz_func:
            mocked_bz_func.return_value = private_webhook_request_example.bug
            with TestClient(app) as anon_client:
                # https://fastapi.tiangolo.com/advanced/testing-events/

                response = anon_client.post(
                    "/bugzilla_webhook", data=private_webhook_request_example.json()
                )
                assert response
                assert response.status_code == 200

                payload = BugzillaWebhookRequest.parse_raw(response.json()["payload"])
                assert payload.bug.id == 654321


def test_request_is_ignored_because_no_bug(
    caplog, webhook_request_example: BugzillaWebhookRequest
):
    with TestClient(app) as anon_client:
        # https://fastapi.tiangolo.com/advanced/testing-events/
        invalid_webhook_request_example = webhook_request_example
        invalid_webhook_request_example.bug = None

        response = anon_client.post(
            "/bugzilla_webhook", data=invalid_webhook_request_example.json()
        )
        assert response
        assert response.status_code == 200
        assert response.json()["error"] == "no bug data received"

        captured_log_msgs = [
            r.msg % r.args for r in caplog.records if r.name == "src.jbi.router"
        ]

        assert captured_log_msgs == [
            "Handling incoming request",
            "Ignore incoming request: no bug data received",
        ]


def test_request_is_ignored_because_no_action(
    caplog, webhook_request_example: BugzillaWebhookRequest
):
    with mock.patch("src.jbi.whiteboard_actions.default.get_jira") as mocked_jira:
        with mock.patch("src.jbi.router.getbug_as_bugzilla_object") as mocked_bz_func:
            mocked_bz_func = MagicMock()
            mocked_bz_func.return_value = webhook_request_example.bug
            with TestClient(app) as anon_client:
                # https://fastapi.tiangolo.com/advanced/testing-events/
                invalid_webhook_request_example = webhook_request_example
                invalid_webhook_request_example.bug.whiteboard = ""  # type: ignore

                response = anon_client.post(
                    "/bugzilla_webhook", data=invalid_webhook_request_example.json()
                )
                assert response
                assert response.status_code == 200
                assert (
                    response.json()["error"]
                    == "whiteboard tag not found in configured actions"
                )

                captured_log_msgs = [
                    r.msg % r.args for r in caplog.records if r.name == "src.jbi.router"
                ]

                assert captured_log_msgs == [
                    "Handling incoming request",
                    "Ignore incoming request: whiteboard tag not found in configured actions",
                ]
