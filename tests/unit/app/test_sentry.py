"""
Module for testing Sentry integration
"""
# pylint: disable=cannot-enumerate-pytest-fixtures

import json
import os.path
from unittest.mock import patch


def test_errors_are_reported_to_sentry(anon_client):
    with patch("src.app.api.sentry_sdk.capture_exception") as mocked:
        with patch("src.jbi.router.execute_request", side_effect=ValueError):
            try:
                anon_client.post("/bugzilla_webhook")
            except Exception:
                pass  # This shouldn't be necessary, the API should return 500.

    assert mocked.called
