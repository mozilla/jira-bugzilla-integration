"""
Module for testing src/app/api.py
"""
# pylint: disable=cannot-enumerate-pytest-fixtures

from fastapi.testclient import TestClient

from src.app.api import app


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
