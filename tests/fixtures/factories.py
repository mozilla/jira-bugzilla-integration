from secrets import token_hex

import pytest

from jbi import Operation, models


@pytest.fixture
def action_params_factory():
    def _action_params_factory(**overrides):
        params = {
            "jira_project_key": "JBI",
            "jira_components": [],
            "labels_brackets": "no",
            "status_map": {},
            "resolution_map": {},
            "issue_type_map": {"task": "Task", "defect": "Bug"},
            **overrides,
        }
        return models.ActionParams.parse_obj(params)

    return _action_params_factory


@pytest.fixture
def action_factory(action_params_factory):
    def _action_factory(**overrides):
        action = {
            "whiteboard_tag": "devtest",
            "bugzilla_user_id": "tbd",
            "description": "test config",
            "parameters": action_params_factory(),
            **overrides,
        }
        return models.Action.parse_obj(action)

    return _action_factory


@pytest.fixture
def bug_factory():
    def _bug_factory(**overrides):
        bug = {
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
            "whiteboard": "[devtest]",
            **overrides,
        }
        return models.BugzillaBug.parse_obj(bug)

    return _bug_factory


@pytest.fixture
def webhook_user_factory():
    def _webhook_user_factory(**overrides):
        user = {
            "id": 123456,
            "login": "nobody@mozilla.org",
            "real_name": "Nobody [ :nobody ]",
            **overrides,
        }
        return models.BugzillaWebhookUser.parse_obj(user)

    return _webhook_user_factory


@pytest.fixture
def webhook_event_factory(webhook_user_factory):
    def _webhook_event_factory(**overrides):
        event = {
            "action": "create",
            "changes": None,
            "routing_key": "bug.create",
            "target": "bug",
            "time": "2022-03-23T20:10:17.495000+00:00",
            "user": webhook_user_factory(),
            **overrides,
        }
        return models.BugzillaWebhookEvent.parse_obj(event)

    return _webhook_event_factory


@pytest.fixture
def webhook_event_change_factory():
    def _webhook_event_change_factory(**overrides):
        event = {
            "field": "field",
            "removed": "old value",
            "added": "new value",
            **overrides,
        }
        return models.BugzillaWebhookEventChange.parse_obj(event)

    return _webhook_event_change_factory


@pytest.fixture
def webhook_factory(bug_factory, webhook_event_factory):
    def _webhook_factory(**overrides):
        webhook_event = {
            "bug": bug_factory(),
            "event": webhook_event_factory(),
            "webhook_id": 34,
            "webhook_name": "local-test",
            **overrides,
        }
        return models.BugzillaWebhookRequest.parse_obj(webhook_event)

    return _webhook_factory


@pytest.fixture
def comment_factory():
    def _comment_factory(**overrides):
        return models.BugzillaComment.parse_obj(
            {
                "id": 343,
                "text": "comment text",
                "bug_id": 654321,
                "count": 1,
                "is_private": True,
                "creator": "mathieu@mozilla.org",
                **overrides,
            }
        )

    return _comment_factory


@pytest.fixture
def action_context_factory(
    action_factory, bug_factory, webhook_event_factory, jira_context_factory
):
    def _action_context_factory(**overrides):
        return models.ActionContext.parse_obj(
            {
                "action": action_factory(),
                "rid": token_hex(16),
                "operation": Operation.IGNORE,
                "bug": bug_factory(),
                "event": webhook_event_factory(),
                "jira": jira_context_factory(),
                **overrides,
            },
        )

    return _action_context_factory


@pytest.fixture
def jira_context_factory():
    def _jira_context_factory(**overrides):
        return models.JiraContext.parse_obj(
            {
                "project": "JBI",
                "issue": None,
                **overrides,
            }
        )

    return _jira_context_factory


@pytest.fixture
def bugzilla_webhook_factory():
    def _bugzilla_webhook_factory(**overrides):
        return models.BugzillaWebhook.parse_obj(
            {
                "component": "General",
                "creator": "admin@mozilla.bugs",
                "enabled": True,
                "errors": 0,
                "event": "create,change,attachment,comment",
                "id": 1,
                "name": "Test Webhooks",
                "product": "Firefox",
                "url": "http://server.example.com/bugzilla_webhook",
                **overrides,
            }
        )

    return _bugzilla_webhook_factory


__all__ = [
    "action_params_factory",
    "action_factory",
    "bug_factory",
    "webhook_user_factory",
    "webhook_event_factory",
    "webhook_event_change_factory",
    "webhook_factory",
    "comment_factory",
    "action_context_factory",
    "jira_context_factory",
    "bugzilla_webhook_factory",
]
