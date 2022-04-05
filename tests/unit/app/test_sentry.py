"""
Module for testing Sentry integration
"""
# pylint: disable=cannot-enumerate-pytest-fixtures

import json
import os.path
from unittest.mock import patch

import pytest


def test_errors_are_reported_to_sentry(anon_client):
    with patch("sentry_sdk.hub.Hub.capture_event") as mocked:
        with patch("src.jbi.router.execute_action", side_effect=ValueError):
            with pytest.raises(ValueError):
                anon_client.post("/bugzilla_webhook")

    assert mocked.called
