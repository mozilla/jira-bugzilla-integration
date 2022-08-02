"""
Module for setting up pytest fixtures
"""
import time
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from jbi.app import app
from jbi.bugzilla import BugzillaWebhookComment, BugzillaWebhookRequest
from jbi.environment import Settings
from jbi.models import Action, Actions


@pytest.fixture
def anon_client():
    """A test client with no authorization."""
    return TestClient(app)


@pytest.fixture
def settings():
    """A test Settings object"""
    return Settings()


@pytest.fixture(autouse=True)
def mocked_bugzilla():
    with mock.patch("jbi.services.rh_bugzilla.Bugzilla") as mocked_bz:
        yield mocked_bz


@pytest.fixture(autouse=True)
def mocked_jira():
    with mock.patch("jbi.services.CustomJira") as mocked_jira:
        yield mocked_jira


@pytest.fixture
def webhook_create_example(mocked_bugzilla) -> BugzillaWebhookRequest:
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
                "summary": "JBI Test",
                "type": "defect",
                "whiteboard": "devtest",
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

    mocked_bugzilla().getbug.return_value = webhook_payload.bug
    mocked_bugzilla().get_comments.return_value = {
        "bugs": {"654321": {"comments": [{"text": "Initial comment"}]}}
    }

    return webhook_payload


@pytest.fixture
def webhook_comment_example(webhook_create_example) -> BugzillaWebhookRequest:
    webhook_comment_example: BugzillaWebhookRequest = webhook_create_example
    webhook_comment_example.event.target = "comment"
    webhook_comment_example.event.user.login = "mathieu@mozilla.org"  # type: ignore
    assert webhook_comment_example.bug
    webhook_comment_example.bug.comment = BugzillaWebhookComment.parse_obj(
        {"number": 2, "body": "hello"}
    )
    webhook_comment_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]
    return webhook_comment_example


@pytest.fixture
def webhook_private_comment_example(
    webhook_create_example, mocked_bugzilla
) -> BugzillaWebhookRequest:
    webhook_comment_example: BugzillaWebhookRequest = webhook_create_example
    webhook_comment_example.event.target = "comment"
    webhook_comment_example.event.user.login = "mathieu@mozilla.org"  # type: ignore
    assert webhook_comment_example.bug
    webhook_comment_example.bug.comment = BugzillaWebhookComment.parse_obj(
        {"id": 344, "number": 2, "is_private": True}
    )
    webhook_comment_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]

    # Call to Bugzilla returns the actual bug comments.
    mocked_bugzilla().get_comments.return_value = {
        "bugs": {
            str(webhook_comment_example.bug.id): {
                "comments": [
                    {
                        "id": 343,
                        "text": "not this one",
                        "bug_id": webhook_comment_example.bug.id,
                        "count": 1,
                        "is_private": True,
                        "creator": "mathieu@mozilla.org",
                    },
                    {
                        "id": 344,
                        "text": "hello",
                        "bug_id": webhook_comment_example.bug.id,
                        "count": 2,
                        "is_private": True,
                        "creator": "mathieu@mozilla.org",
                    },
                    {
                        "id": 345,
                        "text": "or this one",
                        "bug_id": webhook_comment_example.bug.id,
                        "count": 3,
                        "is_private": True,
                        "creator": "mathieu@mozilla.org",
                    },
                ]
            }
        },
        "comments": {},
    }

    return webhook_comment_example


@pytest.fixture
def webhook_create_private_example(
    webhook_create_example, mocked_bugzilla
) -> BugzillaWebhookRequest:
    private_bug = webhook_create_example.bug
    private_bug.is_private = True
    # Call to Bugzilla returns the actual bug object.
    mocked_bugzilla().getbug.return_value = private_bug

    # But webhook payload only contains private attribute.
    webhook_create_private_example = BugzillaWebhookRequest.parse_obj(
        {
            **webhook_create_example.dict(),
            "bug": {"id": private_bug.id, "is_private": True},
        }
    )
    return webhook_create_private_example


@pytest.fixture
def webhook_modify_example(webhook_create_example) -> BugzillaWebhookRequest:
    webhook_modify_example: BugzillaWebhookRequest = webhook_create_example
    assert webhook_modify_example.bug
    webhook_modify_example.bug.see_also = [
        "https://mozilla.atlassian.net/browse/JBI-234"
    ]

    webhook_modify_example.event.action = "modify"
    webhook_modify_example.event.routing_key = "bug.modify:status"
    return webhook_modify_example


@pytest.fixture
def webhook_change_status_assignee(webhook_modify_example):
    payload = webhook_modify_example.dict()
    payload["event"]["routing_key"] = "bug.modify"
    payload["event"]["changes"] = [
        {"field": "status", "removed": "OPEN", "added": "FIXED"},
        {
            "field": "assignee",
            "removed": "nobody@mozilla.org",
            "added": "mathieu@mozilla.com",
        },
    ]
    return BugzillaWebhookRequest.parse_obj(payload)


@pytest.fixture
def webhook_modify_private_example(
    webhook_modify_example, mocked_bugzilla
) -> BugzillaWebhookRequest:
    private_bug = webhook_modify_example.bug
    private_bug.is_private = True
    # Call to Bugzilla returns the actual bug object.
    mocked_bugzilla().getbug.return_value = private_bug

    # But webhook payload only contains private attribute.
    webhook_modify_private_example = BugzillaWebhookRequest.parse_obj(
        {
            **webhook_modify_example.dict(),
            "bug": {"id": private_bug.id, "is_private": True},
        }
    )
    return webhook_modify_private_example


@pytest.fixture
def action_example() -> Action:
    return Action.parse_obj(
        {
            "whiteboard_tag": "devtest",
            "contact": "contact@corp.com",
            "description": "test config",
            "module": "tests.fixtures.noop_action",
        }
    )


@pytest.fixture
def actions_example(action_example) -> Actions:
    return Actions.parse_obj([action_example])


@pytest.fixture(autouse=True)
def sleepless(monkeypatch):
    # https://stackoverflow.com/a/54829577
    monkeypatch.setattr(time, "sleep", lambda s: None)


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
