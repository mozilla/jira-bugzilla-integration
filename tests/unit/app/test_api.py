from fastapi.testclient import TestClient

from src.app.api import app


def test_read_root(anon_client):
    """The site root redirects to the Swagger docs"""
    resp = anon_client.get("/")
    assert resp.status_code == 200
    assert len(resp.history) == 1
    prev_resp = resp.history[0]
    assert prev_resp.status_code == 307  # Temporary Redirect
    assert prev_resp.headers["location"] == "./docs"


def test_request_summary_is_logged(caplog):
    with TestClient(app) as anon_client:
        # https://fastapi.tiangolo.com/advanced/testing-events/
        anon_client.get("/__lbheartbeat__")

        summary = caplog.records[-1]

        assert summary.name == "request.summary"
        assert summary.method == "GET"
        assert summary.path == "/__lbheartbeat__"
        assert summary.querystring == {}
