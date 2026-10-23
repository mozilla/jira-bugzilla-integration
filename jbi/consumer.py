"""Pull events from a Pub/Sub subscription and feed them to the ingest seam.

Modelled on the reference consumer the repo admin supplied, including two
lessons that were learned the hard way there and are reproduced deliberately:

1. **Flow control must allow concurrency.** With `max_messages=1` every
   ordering key is serialised behind a single slot, so a backlog cannot drain
   before the pull window closes and held ordered messages are stranded. The
   subscription's ordering keys still guarantee per-key order; concurrency
   only parallelises *across* keys.
2. **Messages still held for an ordering key at window teardown are
   abandoned** by the client and redelivered later, and the client logs this
   without any context of ours. If it happens often the pull window is too
   short for the inbound backlog, so this module says so explicitly in the
   log rather than leaving an unexplained gap.

The consumer runs as a separate process (`python -m jbi consume`) alongside
the web service, which keeps serving the webhook endpoints and health checks.
"""

import json
import logging
import signal
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any, Callable, Optional

from jbi.bugzilla import models as bugzilla_models
from jbi.configuration import get_actions
from jbi.environment import get_settings
from jbi.ingest import EventSource, InboundEvent, IngestOutcome, IngestResult
from jbi.jira_inbound.models import JiraWebhookRequest
from jbi.models import Actions

logger = logging.getLogger(__name__)


class UndecodableMessage(Exception):
    """The message can never be processed, however many times it is retried.

    Distinguished from an outage so the caller acks instead of asking for a
    redelivery that would fail identically.
    """


@dataclass
class DecodedMessage:
    """A Pub/Sub message resolved into something the core can ingest."""

    event: InboundEvent
    delivery_id: str


def detect_source(payload: dict[str, Any], attributes: dict[str, str]) -> EventSource:
    """Decide which system a message describes.

    The `event_source` attribute is authoritative, matching the reference
    implementation's convention. Payload shape is a fallback for publishers
    that do not set it: the two are structurally unmistakable, since a
    Bugzilla webhook carries `bug` and `event` while a Jira event carries
    `issue` or `webhookEvent`.
    """
    declared = (
        attributes.get("event_source") or attributes.get("source") or ""
    ).lower()
    if declared in ("bugzilla", "bmo"):
        return EventSource.BUGZILLA
    if declared == "jira":
        return EventSource.JIRA
    if declared:
        raise UndecodableMessage(f"unknown event_source {declared!r}")

    if "bug" in payload and "event" in payload:
        return EventSource.BUGZILLA
    if "issue" in payload or "webhookEvent" in payload:
        return EventSource.JIRA
    raise UndecodableMessage(
        "cannot tell whether this is a Bugzilla or Jira event; "
        "publish an `event_source` attribute"
    )


def decode_message(message: Any) -> DecodedMessage:
    """Turn a Pub/Sub message into an `InboundEvent`.

    Raises `UndecodableMessage` for anything redelivery cannot fix.
    """
    attributes = dict(getattr(message, "attributes", {}) or {})

    try:
        raw = message.data.decode("utf-8")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise UndecodableMessage(f"data is not valid UTF-8: {exc}") from exc

    try:
        # See the router: producers interpolate comment bodies into JSON
        # without escaping newlines, so literal control characters are
        # normal traffic rather than corruption.
        payload = json.loads(raw, strict=False)
    except json.JSONDecodeError as exc:
        raise UndecodableMessage(f"data is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise UndecodableMessage("data is not a JSON object")

    source = detect_source(payload, attributes)

    try:
        if source == EventSource.BUGZILLA:
            parsed: Any = bugzilla_models.WebhookRequest.model_validate(payload)
        else:
            parsed = JiraWebhookRequest.model_validate(payload)
    except Exception as exc:
        raise UndecodableMessage(
            f"payload does not match the {source} schema: {exc}"
        ) from exc

    # `delivery_id` is the publisher's own id where present, matching the
    # reference implementation; the broker's message id is the fallback.
    delivery_id = str(
        attributes.get("delivery_id") or getattr(message, "message_id", "") or ""
    )

    return DecodedMessage(
        event=InboundEvent(
            source=source,
            payload=parsed,
            message_id=delivery_id or None,
            delivery_attempt=int(attributes.get("delivery_attempt", 1) or 1),
        ),
        delivery_id=delivery_id,
    )


class MessageProcessor:
    """Decide, for one message, whether it should be acked."""

    def __init__(self, actions: Actions, ingest: Optional[Callable] = None):
        self.actions = actions
        if ingest is None:
            from jbi.ingest import ingest_event

            ingest = ingest_event
        self._ingest = ingest

    def process(self, message: Any) -> bool:
        """Return True to ack, False to nack.

        No dead-letter queue is passed to the seam: the subscription owns
        retries and dead-lettering, and stacking JBI's file queue underneath
        would multiply redeliveries.
        """
        import asyncio

        try:
            decoded = decode_message(message)
        except UndecodableMessage as exc:
            # Ack: redelivery cannot fix a malformed payload, and nacking it
            # would occupy the subscription until the message expired.
            logger.error(
                "Dropping undecodable message %s: %s",
                getattr(message, "message_id", "?"),
                exc,
                extra={"message_id": getattr(message, "message_id", None)},
            )
            return True

        result: IngestResult = asyncio.run(self._ingest(decoded.event, self.actions))

        logger.info(
            "Processed %s message %s -> %s%s",
            decoded.event.source,
            decoded.delivery_id,
            result.outcome,
            f" ({result.reason})" if result.reason else "",
            extra={
                "message_id": decoded.delivery_id,
                "event_source": str(decoded.event.source),
                "outcome": str(result.outcome),
            },
        )

        if result.outcome == IngestOutcome.RETRY:
            return False
        return True


def _is_closed_channel(exc: Exception) -> bool:
    """Whether an exception is the subscriber's shutdown race.

    Word order varies between client versions and code paths ("Channel
    closed", "closed channel"), so both words are checked independently.
    """
    text = str(exc).lower()
    return "channel" in text and "closed" in text


def make_callback(processor: MessageProcessor) -> Callable[[Any], None]:
    """Build the Pub/Sub callback: process, then ack or nack."""

    def callback(message: Any) -> None:
        try:
            should_ack = processor.process(message)
        except Exception:
            # Unknown state, so redeliver rather than drop. Duplicate
            # suppression in the seam guards against double-processing.
            logger.exception(
                "Unexpected error processing message %s",
                getattr(message, "message_id", "?"),
            )
            should_ack = False

        try:
            message.ack() if should_ack else message.nack()
        except ValueError as exc:
            # Raised when the subscriber is already shutting down. Matched on
            # both words independently rather than the literal phrase
            # "closed channel": the client's own message reads "Channel
            # closed", so a phrase match silently misses it and the error
            # escalates during every shutdown.
            if _is_closed_channel(exc):
                logger.warning(
                    "Channel closed before ack/nack of %s (shutting down)",
                    getattr(message, "message_id", "?"),
                )
            else:
                raise
        except Exception as exc:
            # An expired ack id means processing outran the ack deadline; the
            # message is already being redelivered and idempotency covers it.
            if _is_closed_channel(exc):
                logger.warning(
                    "Channel closed before ack/nack of %s (shutting down)",
                    getattr(message, "message_id", "?"),
                )
            elif "INVALID_ACK_ID" in str(exc):
                logger.warning(
                    "Ack id expired for %s (processing exceeded the deadline)",
                    getattr(message, "message_id", "?"),
                )
            else:
                logger.critical(
                    "Failed to ack/nack %s: %s",
                    getattr(message, "message_id", "?"),
                    exc,
                    exc_info=True,
                )

    return callback


class Consumer:
    """Owns the subscriber lifecycle."""

    def __init__(self, actions: Optional[Actions] = None, subscriber: Any = None):
        self.settings = get_settings()
        self.actions = actions if actions is not None else get_actions()

        if subscriber is None:
            from google.cloud import pubsub_v1  # imported lazily: only the

            # consumer process needs the Pub/Sub client, so the web service
            # does not pay for it at import time.
            subscriber = pubsub_v1.SubscriberClient()
        self.subscriber = subscriber
        self.subscription_path = self.subscriber.subscription_path(
            self.settings.pubsub_project_id, self.settings.pubsub_subscription_id
        )
        self.callback = make_callback(MessageProcessor(self.actions))

    def _flow_control(self):
        from google.cloud import pubsub_v1

        return pubsub_v1.types.FlowControl(
            # Never 1: that serialises every ordering key behind one slot, so
            # a backlog cannot drain before the pull window closes and held
            # ordered messages are stranded. Per-key order is preserved by the
            # subscription regardless.
            max_messages=self.settings.pubsub_max_concurrent_messages,
            max_lease_duration=self.settings.pubsub_max_lease_duration,
        )

    def run(self, timeout_seconds: Optional[int] = None) -> None:
        """Listen until the pull window elapses or a signal arrives."""
        if timeout_seconds is None:
            timeout_seconds = self.settings.pubsub_pull_timeout_seconds

        streaming_pull_future = self.subscriber.subscribe(
            self.subscription_path,
            callback=self.callback,
            flow_control=self._flow_control(),
            await_callbacks_on_shutdown=True,
        )

        shutdown_requested = False

        def handle_signal(signum, _frame):
            nonlocal shutdown_requested
            logger.info(
                "Received %s, shutting down gracefully", signal.Signals(signum).name
            )
            shutdown_requested = True
            streaming_pull_future.cancel()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        logger.info(
            "Listening on %s (pull window %ss, concurrency %s)",
            self.subscription_path,
            timeout_seconds,
            self.settings.pubsub_max_concurrent_messages,
        )

        with self.subscriber:
            try:
                streaming_pull_future.result(timeout=timeout_seconds)
            except FuturesTimeoutError:
                logger.warning(
                    "Pull window elapsed; shutting down. Messages still held "
                    "for an ordering key are abandoned and will be "
                    "redelivered. If this recurs, the window is too short for "
                    "the backlog: raise the pull timeout or the concurrency.",
                    extra={
                        "timeout_seconds": timeout_seconds,
                        "max_concurrent_messages": (
                            self.settings.pubsub_max_concurrent_messages
                        ),
                        "status": "pull_window_timeout",
                    },
                )
                self._cancel(streaming_pull_future)
            except KeyboardInterrupt:
                logger.info("Interrupted, shutting down")
                self._cancel(streaming_pull_future)
            except Exception as exc:
                message = str(exc).lower()
                if shutdown_requested and "cancel" in message:
                    logger.info("Subscriber cancelled by shutdown signal")
                elif ("channel" in message and "closed" in message) or (
                    "cancelled" in message
                ):
                    logger.info("Streaming pull closed during shutdown")
                else:
                    logger.error("Error while pulling messages: %s", exc, exc_info=True)
                    raise
            finally:
                logger.info("Consumer stopped")
                # Let background threads finish their ack/nack round trips.
                time.sleep(int(self.settings.pubsub_shutdown_grace_seconds))

    @staticmethod
    def _cancel(streaming_pull_future) -> None:
        streaming_pull_future.cancel()
        try:
            streaming_pull_future.result(timeout=5)
        except Exception:
            # Cancelling raises; that is the expected path.
            pass
