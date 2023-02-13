from secrets import token_hex

from jbi import Operation
from jbi.models import (
    Action,
    ActionContext,
    BugzillaBug,
    BugzillaComment,
    BugzillaWebhookEvent,
    BugzillaWebhookRequest,
    BugzillaWebhookUser,
    JiraContext,
)


def action_factory(**overrides):
    action = {
        "whiteboard_tag": "devtest",
        "contact": "tbd",
        "description": "test config",
        "module": "tests.fixtures.noop_action",
        "parameters": {
            "jira_project_key": "JBI",
        },
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
        "whiteboard": "devtest",
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
