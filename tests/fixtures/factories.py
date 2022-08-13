from jbi.models import (
    BugzillaBug,
    BugzillaWebhookEvent,
    BugzillaWebhookRequest,
    BugzillaWebhookUser,
)


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
    return {
        "id": 343,
        "text": "comment text",
        "bug_id": 654321,
        "count": 1,
        "is_private": True,
        "creator": "mathieu@mozilla.org",
        **overrides,
    }
