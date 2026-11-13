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


# --- Metrics ----------------------------------------------------------------


@pytest.fixture
def consumer_statsd():
    with mock.patch("jbi.consumer.statsd") as patched:
        yield patched


def _names(calls):
    return [call.args[0] for call in calls]


def test_a_handled_message_is_counted_and_timed(actions, consumer_statsd):
    processor = MessageProcessor(
        actions,
        ingest=mock.AsyncMock(return_value=IngestResult(outcome=IngestOutcome.HANDLED)),
    )

    processor.process(FakeMessage(jira_payload()))

    assert _names(consumer_statsd.incr.call_args_list) == [
        "jbi.consumer.received.count",
        "jbi.consumer.outcome.handled.count",
    ]
    assert "jbi.consumer.process.timer" in _names(consumer_statsd.timing.call_args_list)


def test_each_outcome_has_its_own_counter(actions, consumer_statsd):
    for outcome in IngestOutcome:
        processor = MessageProcessor(
            actions,
            ingest=mock.AsyncMock(return_value=IngestResult(outcome=outcome)),
        )
        processor.process(FakeMessage(jira_payload()))

    counted = _names(consumer_statsd.incr.call_args_list)
    for outcome in IngestOutcome:
        assert f"jbi.consumer.outcome.{outcome}.count" in counted


def test_an_undecodable_message_is_counted(actions, consumer_statsd):
    MessageProcessor(actions, ingest=mock.AsyncMock()).process(
        FakeMessage(None, data=b"garbage")
    )

    assert "jbi.consumer.undecodable.count" in _names(
        consumer_statsd.incr.call_args_list
    )


def test_a_redelivery_is_counted(actions, consumer_statsd):
    processor = MessageProcessor(
        actions,
        ingest=mock.AsyncMock(return_value=IngestResult(outcome=IngestOutcome.HANDLED)),
    )
    message = FakeMessage(jira_payload())
    message.delivery_attempt = 3

    processor.process(message)

    assert "jbi.consumer.redelivered.count" in _names(
        consumer_statsd.incr.call_args_list
    )


def test_delivery_attempt_comes_from_the_broker_message():
    message = FakeMessage(jira_payload())
    message.delivery_attempt = 4

    assert decode_message(message).event.delivery_attempt == 4
    # Absent (no dead-letter policy) means the first delivery.
    assert decode_message(FakeMessage(jira_payload())).event.delivery_attempt == 1


def test_freshness_is_recorded_only_for_finished_events(actions, consumer_statsd):
    from datetime import datetime, timedelta, timezone

    published = datetime.now(timezone.utc) - timedelta(seconds=30)

    def run(outcome):
        consumer_statsd.reset_mock()
        message = FakeMessage(jira_payload())
        message.publish_time = published
        MessageProcessor(
            actions,
            ingest=mock.AsyncMock(return_value=IngestResult(outcome=outcome)),
        ).process(message)
        return {
            call.args[0]: call.args[1] for call in consumer_statsd.timing.call_args_list
        }

    finished = run(IngestOutcome.HANDLED)
    retried = run(IngestOutcome.RETRY)

    assert 29_000 < finished["jbi.consumer.end_to_end_lag.timer"] < 60_000
    # A retried event is not delivered yet; counting it would make an outage
    # look like freshness.
    assert "jbi.consumer.end_to_end_lag.timer" not in retried


def test_acks_and_nacks_are_counted(consumer_statsd):
    make_callback(mock.Mock(process=mock.Mock(return_value=True)))(
        FakeMessage(jira_payload())
    )
    make_callback(mock.Mock(process=mock.Mock(return_value=False)))(
        FakeMessage(jira_payload())
    )

    assert _names(consumer_statsd.incr.call_args_list) == [
        "jbi.consumer.acked.count",
        "jbi.consumer.nacked.count",
    ]


# --- Shutdown ---------------------------------------------------------------


def _run_and_capture_handlers(actions, fake_subscriber, settings, future):
    """Run the consumer to the end of its window, returning the signal
    handlers it installed so a test can fire them afterwards."""
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    handlers = {}
    future.result.side_effect = FuturesTimeoutError()
    fake_subscriber.subscribe.return_value = future

    with mock.patch("jbi.consumer.get_settings", return_value=settings):
        consumer = Consumer(actions=actions, subscriber=fake_subscriber)
        with mock.patch("jbi.consumer.time.sleep"):
            with mock.patch(
                "jbi.consumer.signal.signal",
                side_effect=lambda signum, handler: handlers.setdefault(
                    signum, handler
                ),
            ):
                consumer.run(timeout_seconds=0.05)
    return handlers


def test_the_wait_is_sliced_so_ctrl_c_is_seen_promptly():
    """One long blocking wait is not interrupted by Ctrl+C on Windows: Python
    only runs the signal handler when the wait returns. Waiting in short slices
    gives it a chance every second."""
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    future = mock.MagicMock()
    future.result.side_effect = [FuturesTimeoutError(), FuturesTimeoutError(), "done"]

    Consumer._wait(future, timeout_seconds=3600)

    assert future.result.call_count == 3
    for call in future.result.call_args_list:
        assert call.kwargs["timeout"] <= 1.0


def test_the_wait_still_ends_when_the_window_does():
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    future = mock.MagicMock()
    future.result.side_effect = FuturesTimeoutError()

    with pytest.raises(FuturesTimeoutError):
        Consumer._wait(future, timeout_seconds=0.05)


def test_the_first_signal_cancels_without_blocking_and_arms_a_watchdog(
    actions, fake_subscriber, settings
):
    """`cancel()` waits for in-flight messages, so running it inside the signal
    handler blocked the handler -- and with it any further Ctrl+C. It runs on
    its own thread, and a watchdog forces the exit if it never finishes."""
    import signal

    future = mock.MagicMock()
    handlers = _run_and_capture_handlers(actions, fake_subscriber, settings, future)
    future.cancel.reset_mock()

    with mock.patch("jbi.consumer.threading.Timer") as timer:
        handlers[signal.SIGINT](signal.SIGINT, None)

    timer.assert_called_once()
    seconds, _callback = timer.call_args.args
    assert seconds == settings.pubsub_shutdown_force_seconds
    assert timer.return_value.daemon is True
    timer.return_value.start.assert_called_once()
    # The cancel ran on a thread; give it a moment to be scheduled.
    import time as real_time

    deadline = real_time.monotonic() + 2
    while not future.cancel.called and real_time.monotonic() < deadline:
        real_time.sleep(0.01)
    assert future.cancel.called


def test_a_second_signal_exits_immediately(actions, fake_subscriber, settings):
    """The escape hatch: a stuck in-flight message must not make the consumer
    unkillable. Unacknowledged messages are redelivered by Pub/Sub."""
    import signal

    future = mock.MagicMock()
    handlers = _run_and_capture_handlers(actions, fake_subscriber, settings, future)

    with mock.patch("jbi.consumer.threading.Timer"):
        with mock.patch("jbi.consumer.os._exit") as hard_exit:
            handlers[signal.SIGINT](signal.SIGINT, None)
            hard_exit.assert_not_called()
            handlers[signal.SIGINT](signal.SIGINT, None)

    hard_exit.assert_called_once_with(130)


def test_a_cancelled_pull_after_a_signal_is_a_clean_shutdown(
    actions, fake_subscriber, settings, capturelogs
):
    """Cancelling surfaces as a cancellation error with no message text, which
    the old matching on the message missed and reported as a failure."""
    import logging
    import signal
    from concurrent.futures import CancelledError

    future = mock.MagicMock()
    handlers = {}
    fake_subscriber.subscribe.return_value = future

    def fire_signal_then_fail(timeout=None):
        handlers[signal.SIGINT](signal.SIGINT, None)
        raise CancelledError()

    future.result.side_effect = fire_signal_then_fail

    with mock.patch("jbi.consumer.get_settings", return_value=settings):
        consumer = Consumer(actions=actions, subscriber=fake_subscriber)
        with mock.patch("jbi.consumer.time.sleep"):
            with mock.patch("jbi.consumer.threading.Timer"):
                with mock.patch(
                    "jbi.consumer.signal.signal",
                    side_effect=lambda s, h: handlers.setdefault(s, h),
                ):
                    with capturelogs.for_logger("jbi.consumer").at_level(logging.INFO):
                        consumer.run(timeout_seconds=30)

    assert not any(r.levelno >= logging.ERROR for r in capturelogs.records)
    assert any("Consumer stopped" in r.message for r in capturelogs.records)
