"""`POST /jira_webhook` when a topic is configured: validate and queue.

The inline behaviour (no topic) is covered in `test_router.py`; these tests
cover only what changes once the endpoint hands events to Pub/Sub.
"""

from unittest import mock

import pytest

from jbi.configuration import get_actions
from jbi.jira_inbound.models import JiraProject
from jbi.models import Actions
from jbi.publisher import PublishError, get_publisher


class FakePublisher:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def publish(self, body, event):
        self.calls.append((body, event))
        if self.error:
            raise self.error
        return "msg-42"


@pytest.fixture
def statsd():
    with mock.patch("jbi.router.statsd") as patched:
        yield patched


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def publishing_client(app, authenticated_client, publisher, action_factory):
    """An authenticated client whose app publishes to a fake topic, with one
    project (`JBI`) opted into inbound sync."""
    actions = Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            )
        ]
    )
    app.dependency_overrides[get_publisher] = lambda: publisher
    app.dependency_overrides[get_actions] = lambda: actions
    try:
        yield authenticated_client
    finally:
        app.dependency_overrides.pop(get_publisher, None)
        app.dependency_overrides.pop(get_actions, None)


def _post(client, event):
    return client.post(
        "/jira_webhook",
        content=event.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )


def counted(statsd):
    return [call.args[0] for call in statsd.incr.call_args_list]


def test_a_valid_event_is_queued_not_processed(
    publishing_client,
    publisher,
    jira_webhook_event,
    mocked_jira,
    mocked_bugzilla,
    statsd,
):
    response = _post(publishing_client, jira_webhook_event)

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "message_id": "msg-42"}
    ((body, event),) = publisher.calls
    assert event == jira_webhook_event
    assert body == jira_webhook_event.model_dump_json().encode()
    # The web process does none of the consumer's work.
    assert not mocked_jira.get_issue_remote_links.called
    assert not mocked_bugzilla.get_bug.called
    assert counted(statsd) == ["jbi.publisher.received.count"]


def test_the_raw_body_is_what_gets_published(
    publishing_client, publisher, jira_webhook_event
):
    """Not a re-serialisation: the consumer must see the producer's bytes."""
    raw = jira_webhook_event.model_dump_json().replace("{", "{ ", 1).encode()

    publishing_client.post(
        "/jira_webhook", content=raw, headers={"Content-Type": "application/json"}
    )

    assert publisher.calls[0][0] == raw


def test_an_event_from_a_project_that_is_not_enabled_is_ignored(
    publishing_client,
    publisher,
    jira_webhook_request_factory,
    jira_issue_factory,
    statsd,
):
    issue = jira_issue_factory(key="OTHER-1", fields__project=JiraProject(key="OTHER"))
    event = jira_webhook_request_factory(issue=issue)

    response = _post(publishing_client, event)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert publisher.calls == []
    assert "jbi.publisher.ignored.project_not_enabled.count" in counted(statsd)


def test_an_event_without_a_changelog_is_ignored(
    publishing_client, publisher, jira_webhook_request_factory, statsd
):
    event = jira_webhook_request_factory(changelog=None)

    response = _post(publishing_client, event)

    assert response.json()["status"] == "ignored"
    assert publisher.calls == []
    assert "jbi.publisher.ignored.no_changelog.count" in counted(statsd)


def test_an_unparseable_body_is_invalid_and_not_published(
    publishing_client, publisher, statsd
):
    response = publishing_client.post("/jira_webhook", content=b"{not json")

    assert response.json()["status"] == "invalid"
    assert publisher.calls == []
    assert "jbi.publisher.invalid.count" in counted(statsd)


def test_a_failed_publish_is_a_5xx_so_jira_retries(
    publishing_client, publisher, jira_webhook_event
):
    publisher.error = PublishError("deadline exceeded")

    response = _post(publishing_client, jira_webhook_event)

    assert response.status_code == 500


def test_a_wrong_key_is_counted_and_not_published(
    app, publisher, jira_webhook_event, statsd
):
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_publisher] = lambda: publisher
    try:
        response = TestClient(app).post(
            "/jira_webhook",
            content=jira_webhook_event.model_dump_json(),
            headers={"X-Api-Key": "wrong"},
        )
    finally:
        app.dependency_overrides.pop(get_publisher, None)

    assert response.status_code == 401
    assert publisher.calls == []
    assert "jbi.publisher.unauthorized.count" in counted(statsd)
