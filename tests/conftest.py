"""
Module for setting up pytest fixtures
"""
import time
from unittest import mock

import pytest
import responses
from fastapi.testclient import TestClient

from jbi import Operation
from jbi.app import app
from jbi.configuration import get_actions
from jbi.environment import Settings
from jbi.models import (
    Action,
    ActionContext,
    Actions,
    BugzillaWebhookComment,
    BugzillaWebhookRequest,
)
from jbi.services import bugzilla, jira
from tests.fixtures.factories import *


class FilteredLogCaptureFixture(pytest.LogCaptureFixture):
    """A custom implementation to simplify capture
    of logs for a particular logger."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger_name = ""  # root (all)

    @property
    def records(self):
        """Return filtered list of messages"""
        return [
            r
            for r in super().records
            if not self.logger_name or r.name == self.logger_name
        ]

    def for_logger(self, logger_name):
        """Specify logger to filter captured messages"""
        self.logger_name = logger_name
        return self


@pytest.fixture()
def capturelogs(request):
    """A custom log capture that can filter on logger name."""
    result = FilteredLogCaptureFixture(request.node)
    yield result
    result._finalize()


@pytest.fixture(autouse=True)
def mocked_statsd():
    with mock.patch("jbi.services.common.statsd") as _mocked_statsd:
        yield _mocked_statsd


@pytest.fixture
def anon_client():
    """A test client with no authorization."""
    return TestClient(app)


@pytest.fixture
def settings():
    """A test Settings object"""
    return Settings()


@pytest.fixture(autouse=True)
def actions():
    get_actions.cache_clear()
    return get_actions()


@pytest.fixture(autouse=True)
def mocked_bugzilla(request):
    if "no_mocked_bugzilla" in request.keywords:
        yield None
        bugzilla.get_client.cache_clear()
    else:
        with mock.patch("jbi.services.bugzilla.BugzillaClient") as mocked_bz:
            yield mocked_bz()
            bugzilla.get_client.cache_clear()


@pytest.fixture(autouse=True)
def mocked_jira(request):
    if "no_mocked_jira" in request.keywords:
        yield None
        jira.get_client.cache_clear()
    else:
        with mock.patch("jbi.services.jira.JiraClient") as mocked_jira:
            yield mocked_jira()
            jira.get_client.cache_clear()


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def context_create_example(action_context_factory) -> ActionContext:
    return action_context_factory(
        operation=Operation.CREATE,
    )


@pytest.fixture
def context_update_example(
    action_context_factory, bug_factory, jira_context_factory
) -> ActionContext:
    bug = bug_factory(see_also=["https://mozilla.atlassian.net/browse/JBI-234"])
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug=bug,
        jira=jira_context_factory(issue=bug.extract_from_see_also()),
    )
    return context


@pytest.fixture
def context_update_status_assignee(
    webhook_event_factory, action_context_factory, bug_factory, jira_context_factory
) -> ActionContext:
    bug = bug_factory(see_also=["https://mozilla.atlassian.net/browse/JBI-234"])
    changes = [
        {
            "field": "status",
            "removed": "OPEN",
            "added": "FIXED",
        },
        {
            "field": "assignee",
            "removed": "nobody@mozilla.org",
            "added": "mathieu@mozilla.com",
        },
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug=bug,
        event=event,
        jira=jira_context_factory(issue=bug.extract_from_see_also()),
    )
    return context


@pytest.fixture
def context_comment_example(
    webhook_user_factory,
    bug_factory,
    webhook_event_factory,
    action_context_factory,
    jira_context_factory,
) -> ActionContext:
    user = webhook_user_factory(login="mathieu@mozilla.org")
    comment = BugzillaWebhookComment.parse_obj({"number": 2, "body": "hello"})
    bug = bug_factory(
        see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        comment=comment,
    )
    event = webhook_event_factory(target="comment", user=user)
    context = action_context_factory(
        operation=Operation.COMMENT,
        bug=bug,
        event=event,
        jira=jira_context_factory(issue=bug.extract_from_see_also()),
    )
    return context


@pytest.fixture
def context_update_resolution_example(
    bug_factory,
    webhook_event_factory,
    webhook_event_change_factory,
    action_context_factory,
    jira_context_factory,
) -> ActionContext:
    bug = bug_factory(see_also=["https://mozilla.atlassian.net/browse/JBI-234"])
    event = webhook_event_factory(
        action="modify",
        changes=[
            webhook_event_change_factory(
                field="resolution", removed="OPEN", added="FIXED"
            ),
        ],
    )
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug=bug,
        event=event,
        jira=jira_context_factory(issue=bug.extract_from_see_also()),
    )
    return context


@pytest.fixture
def webhook_create_example(webhook_factory) -> BugzillaWebhookRequest:
    webhook_payload = webhook_factory()

    return webhook_payload


@pytest.fixture
def webhook_comment_example(
    webhook_user_factory, bug_factory, webhook_event_factory, webhook_factory
) -> BugzillaWebhookRequest:
    user = webhook_user_factory(login="mathieu@mozilla.org")
    comment = BugzillaWebhookComment.parse_obj({"number": 2, "body": "hello"})
    bug = bug_factory(
        see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        comment=comment,
    )
    event = webhook_event_factory(target="comment", user=user)
    webhook_payload = webhook_factory(bug=bug, event=event)

    return webhook_payload


@pytest.fixture
def webhook_private_comment_example(
    webhook_user_factory, webhook_event_factory, bug_factory, webhook_factory
) -> BugzillaWebhookRequest:
    user = webhook_user_factory(login="mathieu@mozilla.org")
    event = webhook_event_factory(target="comment", user=user)
    bug = bug_factory(
        comment={"id": 344, "number": 2, "is_private": True},
        see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    webhook_payload = webhook_factory(bug=bug, event=event)
    return webhook_payload


@pytest.fixture
def webhook_change_status_assignee(
    webhook_event_change_factory, webhook_event_factory, webhook_factory
):
    changes = [
        webhook_event_change_factory(field="status", removed="OPEN", added="FIXED"),
        webhook_event_change_factory(
            field="assignee", removed="nobody@mozilla.org", added="mathieu@mozilla.com"
        ),
    ]
    event = webhook_event_factory(routing_key="bug.modify", changes=changes)
    webhook_payload = webhook_factory(event=event)
    return webhook_payload


@pytest.fixture
def action_example(action_factory) -> Action:
    return action_factory()


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
