"""
Module for setting up pytest fixtures
"""
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from src.app.api import app
from src.app.environment import Settings
from src.jbi.models import Actions
from src.jbi.bugzilla import BugzillaWebhookRequest
from src.jbi.services import get_bugzilla


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
    webhook_payload = BugzillaWebhookRequest.parse_obj(
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

    class FakeBugzillaClient:
        def getbug(self, bug_id):
            if bug_id == webhook_payload.bug.id:
                return webhook_payload.bug
            return get_bugzilla().getbug(bug_id)

    with mock.patch(
        "src.jbi.services.get_bugzilla", return_value=FakeBugzillaClient()
    ) as mocked:
        yield webhook_payload


@pytest.fixture
def actions_example() -> Actions:
    return Actions.parse_obj(
        {
            "actions": {
                "devtest": {
                    "action": "tests.unit.jbi.noop_action",
                    "parameters": {
                        "whiteboard_tag": "devtest",
                    },
                }
            }
        }
    )
