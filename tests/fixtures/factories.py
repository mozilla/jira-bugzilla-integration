from datetime import datetime

import factory

import jbi.bugzilla.models as bugzilla_models
from jbi import Operation, models, queue


class PydanticFactory(factory.Factory):
    """
    - factory_instance(**kwargs) -> Model(**kwargs)
    - factory_instance.create(**kwargs) -> Model(**kwargs)
    - factory_instance.build(**kwargs) -> Model.model_construct(**kwargs)

    https://docs.pydantic.dev/latest/api/base_model/#pydantic.main.BaseModel.model_construct
    """

    class Meta:
        abstract = True

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        return model_class.model_construct(**kwargs)


class ActionParamsFactory(PydanticFactory):
    class Meta:
        model = models.ActionParams

    jira_project_key = "JBI"
    jira_components = {}
    labels_brackets = "no"
    status_map = {}
    resolution_map = {}
    issue_type_map = {"task": "Task", "defect": "Bug"}


class ActionFactory(PydanticFactory):
    class Meta:
        model = models.Action

    whiteboard_tag = "devtest"
    bugzilla_user_id = "tbd"
    description = "test config"
    parameters = factory.SubFactory(ActionParamsFactory)


class ActionsFactory(PydanticFactory):
    class Meta:
        model = models.Actions

    root = factory.List([factory.SubFactory(ActionFactory)])


class WebhookCommentFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.WebhookComment

    body = None
    id = None
    number = None
    is_private = None
    creation_time = None


class BugFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.Bug

    class Params:
        with_comment = factory.Trait(comment=factory.SubFactory(WebhookCommentFactory))

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


class WebhookUserFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.WebhookUser

    id = 123456
    login = "nobody@mozilla.org"
    real_name = "Nobody [ :nobody ]"


class WebhookEventChangeFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.WebhookEventChange

    field = "field"
    removed = "old value"
    added = "new value"


class WebhookEventFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.WebhookEvent

    action = "create"
    changes = None
    routing_key = "bug.create"
    target = "bug"
    time = "2022-03-23T20:10:17.495000+00:00"
    user = factory.SubFactory(WebhookUserFactory)


class WebhookRequestFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.WebhookRequest

    bug = factory.SubFactory(BugFactory)
    event = factory.SubFactory(WebhookEventFactory)
    webhook_id = 34
    webhook_name = "local-test"


class CommentFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.Comment

    id = 343
    text = "comment text"
    bug_id = 654321
    count = 1
    is_private = True
    creator = "mathieu@mozilla.org"


class JiraContextFactory(PydanticFactory):
    class Meta:
        model = models.JiraContext

    project = "JBI"
    issue = None
    labels = []


class ActionContextFactory(PydanticFactory):
    class Meta:
        model = models.ActionContext

    action = factory.SubFactory(ActionFactory)
    operation = Operation.IGNORE
    bug = factory.SubFactory(BugFactory)
    event = factory.SubFactory(WebhookEventFactory)
    jira = factory.SubFactory(JiraContextFactory)


class WebhookFactory(PydanticFactory):
    class Meta:
        model = bugzilla_models.Webhook

    component = "General"
    creator = "admin@mozilla.bugs"
    enabled = True
    errors = 0
    event = "create,change,attachment,comment"
    id = 1
    name = "Test Webhooks"
    product = "Firefox"
    url = "http://server.example.com/bugzilla_webhook"


class PythonExceptionFactory(PydanticFactory):
    class Meta:
        model = queue.PythonException

    type = "ValueError"
    description = "boom!"
    details = "Traceback: foo"


class QueueItemFactory(PydanticFactory):
    class Meta:
        model = queue.QueueItem

    timestamp = datetime.now()
    retries = 0
    payload = factory.SubFactory(WebhookRequestFactory)
    error = factory.SubFactory(PythonExceptionFactory)
