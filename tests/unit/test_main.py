import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from jbi.app import app
from jbi.models import BugzillaWebhookRequest


def test_request_summary_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app) as anon_client:
            # https://fastapi.tiangolo.com/advanced/testing-events/
            anon_client.get("/")

            summary = caplog.records[-1]

            assert summary.name == "request.summary"
            assert summary.method == "GET"
            assert summary.path == "/"
            assert summary.querystring == "{}"


def test_errors_are_reported_to_sentry(
    anon_client, webhook_create_example: BugzillaWebhookRequest
):
    with patch("sentry_sdk.hub.Hub.capture_event") as mocked:
        with patch("jbi.router.execute_action", side_effect=ValueError):
            with pytest.raises(ValueError):
                anon_client.post(
                    "/bugzilla_webhook", data=webhook_create_example.json()
                )

    assert mocked.called, "Sentry captured the exception"
