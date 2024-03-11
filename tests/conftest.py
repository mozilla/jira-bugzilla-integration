"""
Module for setting up pytest fixtures
"""

import time
from unittest import mock

import pytest
import pytest_asyncio
import responses
from fastapi.testclient import TestClient
from pytest_factoryboy import register

import jbi.app
import tests.fixtures.factories as factories
from jbi import Operation, bugzilla, jira
from jbi.environment import Settings
from jbi.models import ActionContext
from jbi.queue import get_dl_queue


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
    with mock.patch("jbi.common.instrument.statsd") as _mocked_statsd:
        yield _mocked_statsd


@pytest_asyncio.fixture(autouse=True)
async def purge_dl_queue():
    q = get_dl_queue()
    await q.backend.clear()


register(factories.ActionContextFactory)
register(factories.ActionFactory)
register(factories.ActionsFactory, "_actions")
register(factories.ActionParamsFactory)
register(factories.BugFactory)
register(factories.WebhookFactory, "bugzilla_webhook")
register(factories.CommentFactory)
register(factories.JiraContextFactory)
register(factories.WebhookEventFactory)
register(factories.WebhookEventChangeFactory)
register(factories.WebhookRequestFactory, "bugzilla_webhook_request")
register(factories.WebhookUserFactory)
register(factories.QueueItemFactory)


register(
    factories.ActionContextFactory, "context_create_example", operation=Operation.CREATE
)


@pytest.fixture
def app():
    return jbi.app.app


@pytest.fixture
def anon_client(app):
    """A test client with no authorization."""
    return TestClient(app)


@pytest.fixture
def test_api_key():
    # api key for tests defined in .env.example
    return "fake_api_key"


@pytest.fixture
def authenticated_client(app, test_api_key):
    """An test client with a valid API key."""
    return TestClient(app, headers={"X-Api-Key": test_api_key})


@pytest.fixture
def settings():
    """A test Settings object"""
    return Settings()


@pytest.fixture()
def actions(actions_factory):
    return actions_factory()


@pytest.fixture(autouse=True)
def mocked_bugzilla(request):
    if "no_mocked_bugzilla" in request.keywords:
        yield None
        bugzilla.get_service.cache_clear()
    else:
        with mock.patch("jbi.bugzilla.service.BugzillaClient") as mocked_bz:
            yield mocked_bz()
            bugzilla.get_service.cache_clear()


@pytest.fixture(autouse=True)
def mocked_jira(request):
    if "no_mocked_jira" in request.keywords:
        yield None
        jira.get_service.cache_clear()
    else:
        with mock.patch("jbi.jira.service.JiraClient") as mocked_jira:
            yield mocked_jira()
            jira.get_service.cache_clear()


@pytest.fixture
def mocked_responses():
    with responses.RequestsMock() as rsps:
        yield rsps


@pytest.fixture
def context_comment_example(action_context_factory) -> ActionContext:
    return action_context_factory(
        operation=Operation.COMMENT,
        bug__see_also=["https://mozilla.atlassian.net/browse/JBI-234"],
        bug__with_comment=True,
        bug__comment__number=2,
        bug__comment__body="> hello\n>\n\nworld",
        event__target="comment",
        event__user__login="mathieu@mozilla.org",
        jira__issue="JBI-234",
    )


@pytest.fixture(autouse=True)
def sleepless(monkeypatch):
    # https://stackoverflow.com/a/54829577
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture
def exclude_middleware(app):
    # Hack to work around issue with Starlette issue on Jinja templates
    # https://github.com/encode/starlette/issues/472#issuecomment-704188037
    user_middleware = app.user_middleware.copy()
    app.user_middleware = []
    app.middleware_stack = app.build_middleware_stack()
    yield
    app.user_middleware = user_middleware
    app.middleware_stack = app.build_middleware_stack()
