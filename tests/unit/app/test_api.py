"""
Module for testing src/app/api.py
"""
import logging
from datetime import datetime

# pylint: disable=cannot-enumerate-pytest-fixtures
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from src.app.api import app
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.errors import IgnoreInvalidRequestError
from src.jbi.models import Actions


@pytest.fixture
def exclude_middleware():
    # Hack to work around issue with Starlette issue on Jinja templates
    # https://github.com/encode/starlette/issues/472#issuecomment-704188037
    user_middleware = app.user_middleware.copy()
    app.user_middleware = []
    app.middleware_stack = app.build_middleware_stack()
    yield
    app.user_middleware = user_middleware
    app.middleware_stack = app.build_middleware_stack()


def test_read_root(anon_client):
    """The root URL provides information"""
    resp = anon_client.get("/")
    infos = resp.json()

    assert "atlassian.net" in infos["configuration"]["jira_base_url"]


def test_request_summary_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app) as anon_client:
            # https://fastapi.tiangolo.com/advanced/testing-events/
            anon_client.get("/__lbheartbeat__")

            summary = caplog.records[-1]

            assert summary.name == "request.summary"
            assert summary.method == "GET"
            assert summary.path == "/__lbheartbeat__"
            assert summary.querystring == {}


def test_whiteboard_tags(anon_client):
    resp = anon_client.get("/whiteboard_tags")
    actions = resp.json()

    assert actions["devtest"]["description"] == "DevTest whiteboard tag"


def test_jira_projects(anon_client, mocked_jira):
    mocked_jira().projects.return_value = [{"key": "Firefox"}, {"key": "Fenix"}]

    resp = anon_client.get("/jira_projects/")
    infos = resp.json()

    assert infos == ["Firefox", "Fenix"]


def test_whiteboard_tags_filtered(anon_client):
    resp = anon_client.get("/whiteboard_tags/?whiteboard_tag=devtest")
    infos = resp.json()
    assert sorted(infos.keys()) == ["devtest"]

    resp = anon_client.get("/whiteboard_tags/?whiteboard_tag=foo")
    infos = resp.json()
    assert sorted(infos.keys()) == ["devtest"]


def test_powered_by_jbi(exclude_middleware, anon_client):
    resp = anon_client.get("/powered_by_jbi/")
    html = resp.text
    assert "<title>Powered by JBI</title>" in html
    assert 'href="/static/styles.css"' in html
    assert "DevTest" in html


def test_powered_by_jbi_filtered(exclude_middleware, anon_client):
    resp = anon_client.get("/powered_by_jbi/?enabled=false")
    html = resp.text
    assert "DevTest" not in html


def test_statics_are_served(anon_client):
    resp = anon_client.get("/static/styles.css")
    assert resp.status_code == 200


def test_webhook_is_200_if_action_succeeds(
    webhook_create_example: BugzillaWebhookRequest,
    mocked_jira,
    mocked_bugzilla,
):
    mocked_bugzilla().update_bugs.return_value = {
        "bugs": [
            {
                "changes": {
                    "see_also": {
                        "added": "https://mozilla.atlassian.net/browse/JBI-1922",
                        "removed": "",
                    }
                },
                "last_change_time": datetime.now(),
            }
        ]
    }
    mocked_jira().create_or_update_issue_remote_links.return_value = {
        "id": 18936,
        "self": "https://mozilla.atlassian.net/rest/api/2/issue/JBI-1922/remotelink/18936",
    }

    with TestClient(app) as anon_client:
        response = anon_client.post(
            "/bugzilla_webhook", data=webhook_create_example.json()
        )
        assert response
        assert response.status_code == 200


def test_webhook_is_200_if_action_raises_IgnoreInvalidRequestError(
    webhook_create_example: BugzillaWebhookRequest,
):
    assert webhook_create_example.bug
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
