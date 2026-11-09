"""Tests for publishing inbound Jira events to Pub/Sub."""

import json
from unittest import mock

import pytest

from jbi.consumer import decode_message
from jbi.ingest import EventSource
from jbi.publisher import (
    DELIVERY_ID_ATTRIBUTE,
    EVENT_SOURCE_ATTRIBUTE,
    JiraEventPublisher,
    PublishError,
    delivery_id,
)
from tests.unit.test_consumer import FakeMessage


class FakeFuture:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def result(self, timeout=None):
        if self._error:
            raise self._error
        return self._result


class FakePublisherClient:
    def __init__(self, error=None):
        self.error = error
        self.published = []
        self.resumed = []

    def topic_path(self, project, topic):
        return f"projects/{project}/topics/{topic}"

    def publish(self, topic, data, ordering_key="", **attributes):
        self.published.append(
            {
                "topic": topic,
                "data": data,
                "ordering_key": ordering_key,
                "attributes": attributes,
            }
        )
        return FakeFuture(result="msg-1", error=self.error)

    def resume_publish(self, topic, ordering_key):
        self.resumed.append((topic, ordering_key))


@pytest.fixture
def pubsub_settings(settings):
    return settings.model_copy(
        update={"pubsub_project_id": "proj", "pubsub_topic_id": "jira-events"}
    )


@pytest.fixture
def client():
    return FakePublisherClient()


@pytest.fixture
def publisher(pubsub_settings, client):
    return JiraEventPublisher(pubsub_settings, client=client)


def _body(event) -> bytes:
    return event.model_dump_json().encode()


def test_publishes_the_body_to_the_topic_with_the_issue_as_ordering_key(
    publisher, client, jira_webhook_event
):
    message_id = publisher.publish(_body(jira_webhook_event), jira_webhook_event)

    assert message_id == "msg-1"
    (sent,) = client.published
    assert sent["topic"] == "projects/proj/topics/jira-events"
    assert sent["data"] == _body(jira_webhook_event)
    assert sent["ordering_key"] == "JBI-234"
    assert sent["attributes"][EVENT_SOURCE_ATTRIBUTE] == "jira"
    assert sent["attributes"][DELIVERY_ID_ATTRIBUTE] == delivery_id(jira_webhook_event)


def test_published_message_decodes_to_the_same_event_in_the_consumer(
    publisher, client, jira_webhook_event
):
    """The contract between the two halves: what the publisher sends, the
    consumer must turn back into the same event, with a stable id."""
    publisher.publish(_body(jira_webhook_event), jira_webhook_event)
    (sent,) = client.published

    decoded = decode_message(
        FakeMessage(None, data=sent["data"], attributes=sent["attributes"])
    )

    assert decoded.event.source == EventSource.JIRA
    assert decoded.event.payload == jira_webhook_event
    assert decoded.event.message_id == delivery_id(jira_webhook_event)


def test_a_failed_publish_raises_and_resumes_the_ordering_key(
    pubsub_settings, jira_webhook_event
):
    client = FakePublisherClient(error=RuntimeError("deadline exceeded"))
    publisher = JiraEventPublisher(pubsub_settings, client=client)

    with pytest.raises(PublishError):
        publisher.publish(_body(jira_webhook_event), jira_webhook_event)

    # Without this the client keeps rejecting every later event for the issue.
    assert client.resumed == [("projects/proj/topics/jira-events", "JBI-234")]


def test_a_failed_resume_does_not_mask_the_publish_error(
    pubsub_settings, jira_webhook_event
):
    client = FakePublisherClient(error=RuntimeError("deadline exceeded"))
    client.resume_publish = mock.Mock(side_effect=RuntimeError("nope"))
    publisher = JiraEventPublisher(pubsub_settings, client=client)

    with pytest.raises(PublishError, match="deadline exceeded"):
        publisher.publish(_body(jira_webhook_event), jira_webhook_event)


def test_requires_project_and_topic(settings):
    with pytest.raises(ValueError, match="PUBSUB_PROJECT_ID and PUBSUB_TOPIC_ID"):
        JiraEventPublisher(
            settings.model_copy(
                update={"pubsub_project_id": None, "pubsub_topic_id": "t"}
            )
        )


# --- delivery_id ------------------------------------------------------------


def test_delivery_id_is_identical_for_a_retried_delivery(jira_webhook_request_factory):
    first = jira_webhook_request_factory()
    retry = jira_webhook_request_factory()

    assert delivery_id(first) == delivery_id(retry)


def test_delivery_id_differs_when_the_event_differs(jira_webhook_request_factory):
    flip = jira_webhook_request_factory(timestamp=1)
    later_flip = jira_webhook_request_factory(timestamp=2)
    comment = jira_webhook_request_factory(with_comment=True)

    assert len({delivery_id(flip), delivery_id(later_flip), delivery_id(comment)}) == 3


def test_delivery_id_is_a_valid_firestore_document_id(jira_webhook_event):
    value = delivery_id(jira_webhook_event)

    assert len(value) == 64 and "/" not in value
    assert json.dumps(value)  # plain ascii hex
