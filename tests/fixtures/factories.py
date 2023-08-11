from secrets import token_hex

import factory

from jbi import Operation, models


class ActionParamsFactory(factory.Factory):
    class Meta:
        model = models.ActionParams

    jira_project_key = "JBI"
    jira_components = {}
    labels_brackets = "no"
    status_map = {}
    resolution_map = {}
    issue_type_map = {"task": "Task", "defect": "Bug"}


class ActionFactory(factory.Factory):
    class Meta:
        model = models.Action

    whiteboard_tag = "devtest"
    bugzilla_user_id = "tbd"
    description = "test config"
    parameters = factory.SubFactory(ActionParamsFactory)


class ActionsFactory(factory.Factory):
    class Meta:
        model = models.Actions

    root = factory.List([factory.SubFactory(ActionFactory)])


class BugzillaWebhookCommentFactory(factory.Factory):
    class Meta:
        model = models.BugzillaWebhookComment

    body = None
    id = None
    number = None
    is_private = None
    creation_time = None


class BugFactory(factory.Factory):
    class Meta:
        model = models.BugzillaBug

    class Params:
        with_comment = factory.Trait(
            comment=factory.SubFactory(BugzillaWebhookCommentFactory)
        )

    assigned_to = "nobody@mozilla.org"
    comment = None
    component = "General"
    creator = "nobody@mozilla.org"
    flags = []
    id = 654321
    is_private = False
    keywords = []
    priority = ""
    product = "JBI"
    resolution = ""
    see_also = []
    severity = "--"
    status = "NEW"
    summary = "JBI Test"
    type = "defect"
    whiteboard = "[devtest]"


class WebhookUserFactory(factory.Factory):
    class Meta:
        model = models.BugzillaWebhookUser

    id = 123456
    login = "nobody@mozilla.org"
    real_name = "Nobody [ :nobody ]"


class WebhookEventChangeFactory(factory.Factory):
    class Meta:
        model = models.BugzillaWebhookEventChange

    field = "field"
    removed = "old value"
    added = "new value"


class WebhookEventFactory(factory.Factory):
    class Meta:
        model = models.BugzillaWebhookEvent

    action = "create"
    changes = None
    routing_key = "bug.create"
    target = "bug"
    time = "2022-03-23T20:10:17.495000+00:00"
    user = factory.SubFactory(WebhookUserFactory)


class WebhookFactory(factory.Factory):
    class Meta:
        model = models.BugzillaWebhookRequest

    bug = factory.SubFactory(BugFactory)
    event = factory.SubFactory(WebhookEventFactory)
    webhook_id = 34
    webhook_name = "local-test"


class CommentFactory(factory.Factory):
    class Meta:
        model = models.BugzillaComment

    id = 343
    text = "comment text"
    bug_id = 654321
    count = 1
    is_private = True
    creator = "mathieu@mozilla.org"


class JiraContextFactory(factory.Factory):
    class Meta:
        model = models.JiraContext

    project = "JBI"
    issue = None
    labels = []


class ActionContextFactory(factory.Factory):
    class Meta:
        model = models.ActionContext

    action = factory.SubFactory(ActionFactory)
    rid = factory.LazyFunction(lambda: token_hex(16))
    operation = Operation.IGNORE
    bug = factory.SubFactory(BugFactory)
    event = factory.SubFactory(WebhookEventFactory)
    jira = factory.SubFactory(JiraContextFactory)


class BugzillaWebhookFactory(factory.Factory):
    class Meta:
        model = models.BugzillaWebhook

    component = "General"
    creator = "admin@mozilla.bugs"
    enabled = True
    errors = 0
    event = "create,change,attachment,comment"
    id = 1
    name = "Test Webhooks"
    product = "Firefox"
    url = "http://server.example.com/bugzilla_webhook"


__all__ = [
    "ActionContextFactory",
    "ActionFactory",
    "ActionParamsFactory",
    "ActionsFactory",
    "BugFactory",
    "BugzillaWebhookFactory",
    "CommentFactory",
    "JiraContextFactory",
    "WebhookEventChangeFactory",
    "WebhookEventFactory",
    "WebhookFactory",
    "WebhookUserFactory",
]
