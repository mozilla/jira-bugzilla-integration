"""
Module for testing src/app/api.py
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

from fastapi.testclient import TestClient

from src.app.api import app
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.models import Actions


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
    webhook_create_example: BugzillaWebhookRequest,
    mocked_jira,
    mocked_bugzilla,
):
    mocked_bugzilla().update_bugs.return_value = {}
    mocked_jira().create_or_update_issue_remote_links.return_value = {}

    with TestClient(app) as anon_client:
        response = anon_client.post(
            "/bugzilla_webhook", data=webhook_create_example.json()
        )
        assert response
        assert response.status_code == 200


def test_webhook_is_200_if_action_raises_IgnoreInvalidRequestError(
    webhook_create_example: BugzillaWebhookRequest,
):
    webhook_create_example.bug.whiteboard = "unmatched"

    with TestClient(app) as anon_client:
        response = anon_client.post(
            "/bugzilla_webhook", data=webhook_create_example.json()
        )
        assert response
        assert response.status_code == 200
        assert (
            response.json()["error"]
            == "no action matching bug whiteboard tags: unmatched"
        )
