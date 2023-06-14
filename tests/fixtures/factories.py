from secrets import token_hex

from jbi import Operation
from jbi.models import (
    Action,
    ActionContext,
    ActionParams,
    BugzillaBug,
    BugzillaComment,
    BugzillaWebhook,
    BugzillaWebhookEvent,
    BugzillaWebhookEventChange,
    BugzillaWebhookRequest,
    BugzillaWebhookUser,
    JiraContext,
)


def action_params_factory(**overrides):
    params = {
        "jira_project_key": "JBI",
        "jira_components": [],
        "labels_brackets": "no",
        "status_map": {},
        "resolution_map": {},
        "issue_type_map": {"task": "Task", "defect": "Bug"},
        **overrides,
    }
    return ActionParams.parse_obj(params)


def action_factory(**overrides):
    action = {
        "whiteboard_tag": "devtest",
        "bugzilla_user_id": "tbd",
        "description": "test config",
        "module": "tests.fixtures.noop_action",
        "parameters": action_params_factory(),
        **overrides,
    }
    return Action.parse_obj(action)


def bug_factory(**overrides):
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
    return BugzillaBug.parse_obj(bug)


def webhook_user_factory(**overrides):
    user = {
        "id": 123456,
        "login": "nobody@mozilla.org",
        "real_name": "Nobody [ :nobody ]",
        **overrides,
    }
    return BugzillaWebhookUser.parse_obj(user)


def webhook_event_factory(**overrides):
    event = {
        "action": "create",
        "changes": None,
        "routing_key": "bug.create",
        "target": "bug",
        "time": "2022-03-23T20:10:17.495000+00:00",
        "user": webhook_user_factory(),
        **overrides,
    }
    return BugzillaWebhookEvent.parse_obj(event)


def webhook_event_change_factory(**overrides):
    event = {
        "field": "field",
        "removed": "old value",
        "added": "new value",
        **overrides,
    }
    return BugzillaWebhookEventChange.parse_obj(event)


def webhook_factory(**overrides):
    webhook_event = {
        "bug": bug_factory(),
        "event": webhook_event_factory(),
        "webhook_id": 34,
        "webhook_name": "local-test",
        **overrides,
    }
    return BugzillaWebhookRequest.parse_obj(webhook_event)


def comment_factory(**overrides):
    return BugzillaComment.parse_obj(
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


def action_context_factory(**overrides):
    return ActionContext.parse_obj(
        {
            "rid": token_hex(16),
            "operation": Operation.IGNORE,
            "bug": bug_factory(),
            "event": webhook_event_factory(),
            "jira": jira_context_factory(),
            **overrides,
        },
    )


def jira_context_factory(**overrides):
    return JiraContext.parse_obj(
        {
            "project": "JBI",
            "issue": None,
            **overrides,
        }
    )


def bugzilla_webhook_factory(**overrides):
    return BugzillaWebhook.parse_obj(
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
