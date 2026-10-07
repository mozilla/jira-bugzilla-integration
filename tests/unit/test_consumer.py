"""Tests for the Pub/Sub pull consumer."""

import json
from unittest import mock

import pytest

from jbi.consumer import (
    Consumer,
    MessageProcessor,
    UndecodableMessage,
    decode_message,
    detect_source,
    make_callback,
)
from jbi.ingest import EventSource, IngestOutcome, IngestResult, reset_delivery_cache
from jbi.models import Actions


@pytest.fixture(autouse=True)
def clean_delivery_cache():
    reset_delivery_cache()
    yield
    reset_delivery_cache()


class FakeMessage:
    """Stands in for a google.cloud.pubsub_v1 message."""

    def __init__(self, payload, attributes=None, message_id="m-1", data=None):
        self.data = data if data is not None else json.dumps(payload).encode()
        self.attributes = attributes or {}
        self.message_id = message_id
        self.acked = False
        self.nacked = False
        self.ack_error = None

    def ack(self):
        if self.ack_error:
            raise self.ack_error
        self.acked = True

    def nack(self):
        if self.ack_error:
            raise self.ack_error
        self.nacked = True


def bugzilla_payload():
    return {
        "webhook_id": 1,
        "webhook_name": "pubsub",
        "event": {"action": "create", "time": "2026-09-16T00:00:00Z", "target": "bug"},
        "bug": {"id": 654321, "whiteboard": "[devtest]"},
    }


def jira_payload():
    return {
        "webhookEvent": "jira:issue_updated",
        "issue": {"key": "JBI-234", "fields": {"project": {"key": "JBI"}}},
    }


@pytest.fixture
def actions(action_factory):
    return Actions(
        root=[
            action_factory(
                whiteboard_tag="devtest",
                parameters__jira_project_key="JBI",
                parameters__jira_inbound_enabled=True,
            )
        ]
    )


# --- Decoding ---------------------------------------------------------------


def test_event_source_attribute_is_authoritative():
    assert detect_source({}, {"event_source": "bugzilla"}) == EventSource.BUGZILLA
    assert detect_source({}, {"event_source": "jira"}) == EventSource.JIRA
    # `source` is accepted too, since publishers differ.
    assert detect_source({}, {"source": "jira"}) == EventSource.JIRA


def test_shape_sniffing_is_the_fallback():
    assert detect_source(bugzilla_payload(), {}) == EventSource.BUGZILLA
    assert detect_source(jira_payload(), {}) == EventSource.JIRA


def test_unknown_source_is_rejected_rather_than_guessed():
    with pytest.raises(UndecodableMessage):
        detect_source(bugzilla_payload(), {"event_source": "github"})


def test_delivery_id_attribute_is_preferred_over_message_id():
    """Matches the reference implementation: the publisher's own delivery id
    survives a redelivery, whereas the broker message id is per-delivery."""
    message = FakeMessage(
        jira_payload(), attributes={"delivery_id": "pub-77"}, message_id="broker-1"
    )

    decoded = decode_message(message)

    assert decoded.delivery_id == "pub-77"
    assert decoded.event.message_id == "pub-77"


def test_message_id_is_used_when_there_is_no_delivery_id():
    decoded = decode_message(FakeMessage(jira_payload(), message_id="broker-1"))

    assert decoded.delivery_id == "broker-1"


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"\xff\xfe not utf8", "UTF-8"),
        (b"not json", "JSON"),
        (b'"a string"', "JSON object"),
    ],
)
def test_undecodable_payloads_are_flagged(data, expected):
    with pytest.raises(UndecodableMessage) as exc_info:
        decode_message(FakeMessage(None, data=data))

    assert expected in str(exc_info.value)


def test_payload_not_matching_the_schema_is_undecodable():
    message = FakeMessage(
        {"bug": {"nope": 1}, "event": {}}, attributes={"event_source": "bugzilla"}
    )

    with pytest.raises(UndecodableMessage) as exc_info:
        decode_message(message)

    assert "schema" in str(exc_info.value)


# --- Ack decisions ----------------------------------------------------------


def test_ignored_event_is_acked(actions):
    """The decisive rule: an event JBI does not act on is normal traffic.
    Nacking it would redeliver it until it expired."""
    processor = MessageProcessor(
        actions,
        ingest=mock.AsyncMock(
            return_value=IngestResult(outcome=IngestOutcome.IGNORED, reason="no bug")
        ),
    )

    assert processor.process(FakeMessage(jira_payload())) is True


def test_transient_failure_is_nacked(actions):
    processor = MessageProcessor(
        actions,
        ingest=mock.AsyncMock(
            return_value=IngestResult(outcome=IngestOutcome.RETRY, reason="jira down")
        ),
    )

    assert processor.process(FakeMessage(jira_payload())) is False


def test_undecodable_message_is_acked_not_retried(actions, capturelogs):
    """Redelivery cannot fix malformed data, and nacking would occupy the
    subscription until the message expired."""
    import logging

    processor = MessageProcessor(actions, ingest=mock.AsyncMock())

    with capturelogs.for_logger("jbi.consumer").at_level(logging.ERROR):
        acked = processor.process(FakeMessage(None, data=b"garbage"))

    assert acked is True
    assert any("undecodable" in r.message.lower() for r in capturelogs.records)


def test_permanent_failure_is_acked(actions):
    processor = MessageProcessor(
        actions,
        ingest=mock.AsyncMock(
            return_value=IngestResult(outcome=IngestOutcome.PERMANENT_FAILURE)
        ),
    )

    assert processor.process(FakeMessage(jira_payload())) is True


# --- The callback -----------------------------------------------------------


def test_callback_acks_on_success(actions):
    processor = mock.Mock(process=mock.Mock(return_value=True))
    message = FakeMessage(jira_payload())

    make_callback(processor)(message)

    assert message.acked and not message.nacked


def test_callback_nacks_on_retry(actions):
    processor = mock.Mock(process=mock.Mock(return_value=False))
    message = FakeMessage(jira_payload())

    make_callback(processor)(message)

    assert message.nacked and not message.acked


def test_unexpected_error_nacks_rather_than_dropping(actions, capturelogs):
    """Unknown state: redelivering is safer than losing the event, and the
    seam's duplicate suppression guards against double-processing."""
    import logging

    processor = mock.Mock(process=mock.Mock(side_effect=RuntimeError("boom")))
    message = FakeMessage(jira_payload())

    with capturelogs.for_logger("jbi.consumer").at_level(logging.ERROR):
        make_callback(processor)(message)

    assert message.nacked


def test_closed_channel_during_shutdown_is_not_fatal(actions, capturelogs):
    import logging

    processor = mock.Mock(process=mock.Mock(return_value=True))
    message = FakeMessage(jira_payload())
    message.ack_error = ValueError("Channel closed")

    with capturelogs.for_logger("jbi.consumer").at_level(logging.WARNING):
        make_callback(processor)(message)  # must not raise

    assert any("closed" in r.message.lower() for r in capturelogs.records)


def test_expired_ack_id_is_warned_not_crashed(actions, capturelogs):
    """Processing outran the ack deadline; the message is already being
    redelivered, so this is a warning rather than a failure."""
    import logging

    processor = mock.Mock(process=mock.Mock(return_value=True))
    message = FakeMessage(jira_payload())
    message.ack_error = RuntimeError("INVALID_ACK_ID")

    with capturelogs.for_logger("jbi.consumer").at_level(logging.WARNING):
        make_callback(processor)(message)

    assert any("expired" in r.message.lower() for r in capturelogs.records)


# --- Lifecycle --------------------------------------------------------------


@pytest.fixture
def fake_subscriber():
    subscriber = mock.MagicMock()
    subscriber.subscription_path.return_value = "projects/p/subscriptions/s"
    subscriber.__enter__ = mock.Mock(return_value=subscriber)
    subscriber.__exit__ = mock.Mock(return_value=False)
    return subscriber


def test_flow_control_never_serialises_to_one(actions, fake_subscriber, settings):
    """`max_messages=1` serialises every ordering key behind one slot, which
    strands held ordered messages when the pull window closes. Per-key order
    comes from the subscription, not from starving concurrency."""
    with mock.patch("jbi.consumer.get_settings", return_value=settings):
        consumer = Consumer(actions=actions, subscriber=fake_subscriber)
        flow = consumer._flow_control()

    assert flow.max_messages > 1
    assert flow.max_lease_duration == settings.pubsub_max_lease_duration


def test_pull_window_timeout_explains_abandoned_messages(
    actions, fake_subscriber, settings, capturelogs
):
    """The client abandons held ordered messages silently; the operator needs
    to be told what happened and which knob to turn."""
    import logging
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    future = mock.MagicMock()
    future.result.side_effect = FuturesTimeoutError()
    fake_subscriber.subscribe.return_value = future

    with mock.patch("jbi.consumer.get_settings", return_value=settings):
        consumer = Consumer(actions=actions, subscriber=fake_subscriber)
        with mock.patch("jbi.consumer.time.sleep"):
            with capturelogs.for_logger("jbi.consumer").at_level(logging.WARNING):
                consumer.run(timeout_seconds=1)

    assert any("abandoned" in r.message for r in capturelogs.records)
    assert future.cancel.called


def test_subscribe_awaits_callbacks_on_shutdown(actions, fake_subscriber, settings):
    """Without this, in-flight messages are dropped mid-processing when the
    window closes."""
    future = mock.MagicMock()
    fake_subscriber.subscribe.return_value = future

    with mock.patch("jbi.consumer.get_settings", return_value=settings):
        consumer = Consumer(actions=actions, subscriber=fake_subscriber)
        with mock.patch("jbi.consumer.time.sleep"):
            consumer.run(timeout_seconds=1)

    assert fake_subscriber.subscribe.call_args.kwargs["await_callbacks_on_shutdown"]


@pytest.mark.parametrize(
    "text",
    [
        "Channel closed",
        "closed channel",
        "Cannot invoke RPC: Channel closed!",
    ],
)
def test_closed_channel_is_matched_whatever_the_word_order(text, actions):
    """The reference matches the literal phrase "closed channel", but the
    client's own message reads "Channel closed" -- a phrase match misses it
    and the error escalates on every shutdown."""
    processor = mock.Mock(process=mock.Mock(return_value=True))
    message = FakeMessage(jira_payload())
    message.ack_error = ValueError(text)

    make_callback(processor)(message)  # must not raise
