import logging

from fastapi.testclient import TestClient

from src.app.main import app


def test_request_summary_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app) as anon_client:
            # https://fastapi.tiangolo.com/advanced/testing-events/
            anon_client.get("/")

            summary = caplog.records[-1]

            assert summary.name == "request.summary"
            assert summary.method == "GET"
            assert summary.path == "/"
            assert summary.querystring == {}
