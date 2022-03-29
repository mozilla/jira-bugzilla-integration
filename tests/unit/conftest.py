"""
Module for setting up pytest fixtures
"""
import pytest
from fastapi.testclient import TestClient

from src.app.api import app
from src.app.environment import Settings
from src.jbi.bugzilla_objects import BugzillaWebhookRequest


@pytest.fixture
def anon_client():
    """A test client with no authorization."""
    return TestClient(app)


@pytest.fixture
def settings():
    """A test Settings object"""
    return Settings()


@pytest.fixture
def webhook_request_example() -> BugzillaWebhookRequest:
    return BugzillaWebhookRequest.parse_obj(
        {
            "bug": {
                "assigned_to": "nobody@mozilla.org",
                "comment": None,
                "component": "General",
                "creator": "nobody@mozilla.org",
                "flags": [],
                "id": 654321,
                "is_private": False,
                "keywords": [],
                "priority": "",
                "product": "JBI",
                "resolution": "",
                "see_also": [],
                "severity": "--",
                "status": "NEW",
                "summary": "[JBI Test]",
                "type": "defect",
                "whiteboard": "[devtest-]",
            },
            "event": {
                "action": "create",
                "changes": None,
                "routing_key": "bug.create",
                "target": "bug",
                "time": "2022-03-23T20:10:17.495000+00:00",
                "user": {
                    "id": 123456,
                    "login": "nobody@mozilla.org",
                    "real_name": "Nobody [ :nobody ]",
                },
            },
            "webhook_id": 34,
            "webhook_name": "local-test",
        }
    )
