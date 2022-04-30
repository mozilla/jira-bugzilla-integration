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


def test_request_is_ignored_because_private(
    caplog, webhook_request_example: BugzillaWebhookRequest
):
    with TestClient(app) as anon_client:
        # https://fastapi.tiangolo.com/advanced/testing-events/
        invalid_webhook_request_example = webhook_request_example
        invalid_webhook_request_example.bug.is_private = True  # type: ignore

        response = anon_client.post(
            "/bugzilla_webhook", data=invalid_webhook_request_example.json()
        )
        assert response
        assert response.status_code == 200
        assert response.json()["error"] == "private bugs are not valid"

        invalid_request_logs = caplog.records[1]
        assert invalid_request_logs.name == "ignored-requests"

        assert invalid_request_logs.msg == "ignore-invalid-request: %s"
        assert invalid_request_logs.args
        for arg in invalid_request_logs.args:
            assert isinstance(arg, IgnoreInvalidRequestError)
            assert str(arg) == "private bugs are not valid"


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

        invalid_request_logs = caplog.records[1]
        assert invalid_request_logs.name == "ignored-requests"

        assert invalid_request_logs.msg == "ignore-invalid-request: %s"
        assert invalid_request_logs.args
        for arg in invalid_request_logs.args:
            assert isinstance(arg, IgnoreInvalidRequestError)
            assert str(arg) == "no bug data received"


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

                invalid_request_logs = caplog.records[1]
                assert invalid_request_logs.name == "ignored-requests"

                assert invalid_request_logs.msg == "ignore-invalid-request: %s"
                assert invalid_request_logs.args
                for arg in invalid_request_logs.args:
                    assert isinstance(arg, IgnoreInvalidRequestError)
                    assert str(arg) == "whiteboard tag not found in configured actions"
