"""
Module for testing Sentry integration
"""
# pylint: disable=cannot-enumerate-pytest-fixtures

import json
import os.path
from unittest.mock import patch

import pytest

from src.jbi.bugzilla import BugzillaWebhookRequest


def test_errors_are_reported_to_sentry(
    anon_client, webhook_create_example: BugzillaWebhookRequest
):
    with patch("sentry_sdk.hub.Hub.capture_event") as mocked:
        with patch("src.app.router.execute_action", side_effect=ValueError):
            with pytest.raises(ValueError):
                anon_client.post(
                    "/bugzilla_webhook", data=webhook_create_example.json()
                )

    assert mocked.called, "Sentry captured the exception"
