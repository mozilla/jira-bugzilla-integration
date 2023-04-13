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
from tests.fixtures.factories import (
    action_context_factory,
    action_factory,
    bug_factory,
    jira_context_factory,
    webhook_event_factory,
    webhook_factory,
    webhook_user_factory,
)


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
        with mock.patch("jbi.services.jira.Jira") as mocked_jira:
            yield mocked_jira()
            jira.get_client.cache_clear()


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def context_create_example() -> ActionContext:
    return action_context_factory(
        operation=Operation.CREATE,
    )


@pytest.fixture
def context_update_example() -> ActionContext:
    bug = bug_factory(see_also=["https://mozilla.atlassian.net/browse/JBI-234"])
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug=bug,
        jira=jira_context_factory(issue=bug.extract_from_see_also()),
    )
    return context


@pytest.fixture
def context_update_status_assignee() -> ActionContext:
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
def context_comment_example() -> ActionContext:
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
def context_update_resolution_example() -> ActionContext:
    bug = bug_factory(see_also=["https://mozilla.atlassian.net/browse/JBI-234"])
    event = webhook_event_factory(action="modify", routing_key="bug.modify:resolution")
    context = action_context_factory(
        operation=Operation.UPDATE,
        bug=bug,
        event=event,
        jira=jira_context_factory(issue=bug.extract_from_see_also()),
    )
    return context


@pytest.fixture
def webhook_create_example() -> BugzillaWebhookRequest:
    webhook_payload = webhook_factory()

    return webhook_payload


@pytest.fixture
def webhook_comment_example() -> BugzillaWebhookRequest:
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
def webhook_private_comment_example() -> BugzillaWebhookRequest:
    user = webhook_user_factory(login="mathieu@mozilla.org")
    event = webhook_event_factory(target="comment", user=user)
    bug = bug_factory(
        comment={"id": 344, "number": 2, "is_private": True},
        see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
    )
    webhook_payload = webhook_factory(bug=bug, event=event)
    return webhook_payload


@pytest.fixture
def webhook_create_private_example() -> BugzillaWebhookRequest:
    return webhook_factory(
        event=webhook_event_factory(),
        bug={"id": 654321, "is_private": True},
    )


@pytest.fixture
def webhook_change_status_assignee():
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
    webhook_payload = webhook_factory(event=event)
    return webhook_payload


@pytest.fixture
def webhook_modify_private_example() -> BugzillaWebhookRequest:
    event = webhook_event_factory(action="modify", routing_key="bug.modify:status")
    webhook_payload = webhook_factory(
        bug={"id": 654321, "is_private": True}, event=event
    )
    return webhook_payload


@pytest.fixture
def action_example() -> Action:
    return action_factory()


@pytest.fixture
def action_example_with_inner_match_test() -> Action:
    return action_factory(whiteboard_tag="inner-match-test")


@pytest.fixture
def actions_example(action_example) -> Actions:
    return Actions.parse_obj([action_example])


@pytest.fixture
def actions_example_with_inner_match_test(
    action_example_with_inner_match_test,
) -> Actions:
    return Actions.parse_obj([action_example_with_inner_match_test])


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
