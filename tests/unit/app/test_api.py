"""
Module for testing src/app/api.py
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

from fastapi.testclient import TestClient

from src.app.api import app
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.models import Action


def test_read_root(anon_client):
    """The root URL provides information"""
    resp = anon_client.get("/")
    infos = resp.json()

    assert "atlassian.net" in infos["configuration"]["jira_base_url"]


def test_request_summary_is_logged(caplog):
    with TestClient(app) as anon_client:
        # https://fastapi.tiangolo.com/advanced/testing-events/
        anon_client.get("/__lbheartbeat__")

        summary = caplog.records[-1]

        assert summary.name == "request.summary"
        assert summary.method == "GET"
        assert summary.path == "/__lbheartbeat__"
        assert summary.querystring == {}


def test_webhook_is_200_if_action_succeeds(
    webhook_request_example: BugzillaWebhookRequest,
):
    test_action = Action.parse_obj({"action": "tests.unit.jbi.noop_action"})

    with mock.patch("src.jbi.router.getbug_as_bugzilla_object") as mocked_bz_func:
        mocked_bz_func.return_value = webhook_request_example.bug

        with mock.patch("src.jbi.models.Actions.get", return_value=test_action):
            with TestClient(app) as anon_client:
                response = anon_client.post(
                    "/bugzilla_webhook", data=webhook_request_example.json()
                )
                assert response
                assert response.status_code == 200


def test_webhook_is_200_if_action_raises_IgnoreInvalidRequestError(
    webhook_request_example: BugzillaWebhookRequest,
):
    test_action = Action.parse_obj({"action": "tests.unit.jbi.noop_action"})

    with mock.patch("src.jbi.router.getbug_as_bugzilla_object") as mocked_bz_func:
        mocked_bz_func.return_value = webhook_request_example.bug

        with mock.patch("src.jbi.models.Actions.get", return_value=None):
            with TestClient(app) as anon_client:
                response = anon_client.post(
                    "/bugzilla_webhook", data=webhook_request_example.json()
                )
                assert response
                assert response.status_code == 200
                assert (
                    response.json()["error"]
                    == "no action matching bug whiteboard tags: devtest"
                )
