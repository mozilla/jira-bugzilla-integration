"""Publish validated inbound Jira events to Pub/Sub.

`POST /jira_webhook` hands an event here once it has passed the publish-time
gates (`jbi.jira_inbound.preflight`). The consumer (`jbi.consumer`) decodes the
same bytes with the same models, so what is published is the request body
untouched -- no envelope to keep in sync.

Three details carry the guarantees:

* ``delivery_id`` is derived from the event, not from the broker. Jira
  Automation retries a failed delivery with an identical body, which becomes a
  *new* Pub/Sub message with a new ``message_id``; only a deterministic id lets
  the consumer recognise it as the same event.
* The **ordering key is the issue key**, so events of one issue are processed
  in the order they were published.
* A failed publish *pauses* its ordering key in the client. Without
  ``resume_publish`` every later event for that issue fails until the process
  restarts, so a single Pub/Sub blip would silently wedge an issue.
"""

import hashlib
import json
import logging
import time
from functools import lru_cache
from typing import Any, Optional

from statsd.defaults.env import statsd

from jbi.environment import Settings, get_settings
from jbi.jira_inbound.models import JiraWebhookRequest

logger = logging.getLogger(__name__)

EVENT_SOURCE_ATTRIBUTE = "event_source"
DELIVERY_ID_ATTRIBUTE = "delivery_id"


class PublishError(Exception):
    """The event could not be handed to Pub/Sub; the caller should be told to retry."""


def delivery_id(event: JiraWebhookRequest) -> str:
    """Return an id that is the same every time Jira sends this event.

    Built from what identifies the event rather than from the whole body, so
    cosmetic differences between retries cannot split one event into two. The
    Automation payload always carries a millisecond ``timestamp``; without it
    two identical flips of the same field would collapse into one id.
    """
    issue = event.issue
    parts: dict[str, Any] = {
        "issue": issue.key if issue else None,
        "event": event.webhookEvent,
        "timestamp": event.timestamp,
        "comment": event.comment.id if event.comment else None,
        "changes": [
            [item.field, item.fromString, item.toString]
            for item in (event.changelog.items if event.changelog else [])
        ],
    }
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class JiraEventPublisher:
    """Publishes request bodies to the configured topic, in per-issue order."""

    def __init__(self, settings: Settings, client: Optional[Any] = None):
        if not settings.pubsub_topic_id or not settings.pubsub_project_id:
            raise ValueError(
                "PUBSUB_PROJECT_ID and PUBSUB_TOPIC_ID must both be set to publish"
            )
        self.settings = settings
        self._client = client
        self._topic_path: Optional[str] = None

    @property
    def client(self) -> Any:
        if self._client is None:
            # Imported lazily, like the subscriber in `jbi.consumer`: only
            # processes that publish pay for (or need) the Google client.
            from google.cloud import pubsub_v1

            client_options = None
            if self.settings.pubsub_region:
                # Ordering is only guaranteed within one region, so publish to
                # the regional endpoint that matches the topic.
                client_options = {
                    "api_endpoint": f"{self.settings.pubsub_region}-pubsub.googleapis.com:443"
                }
            self._client = pubsub_v1.PublisherClient(
                publisher_options=pubsub_v1.types.PublisherOptions(
                    enable_message_ordering=True
                ),
                client_options=client_options,
            )
        return self._client

    @property
    def topic_path(self) -> str:
        if self._topic_path is None:
            self._topic_path = self.client.topic_path(
                self.settings.pubsub_project_id, self.settings.pubsub_topic_id
            )
        return self._topic_path

    def publish(self, body: bytes, event: JiraWebhookRequest) -> str:
        """Publish one event and return its Pub/Sub message id.

        Blocks until the broker confirms, so a 2xx to Jira means the event is
        durably queued. Raises `PublishError` otherwise.
        """
        issue_key = event.issue.key if event.issue else None
        event_id = delivery_id(event)
        started = time.monotonic()
        try:
            future = self.client.publish(
                self.topic_path,
                body,
                ordering_key=issue_key or "",
                **{
                    EVENT_SOURCE_ATTRIBUTE: "jira",
                    DELIVERY_ID_ATTRIBUTE: event_id,
                },
            )
            message_id = future.result(
                timeout=self.settings.pubsub_publish_timeout_seconds
            )
        except Exception as exc:
            statsd.incr("jbi.publisher.publish_failed.count")
            logger.exception(
                "Failed to publish event on %s (delivery %s)", issue_key, event_id
            )
            self._resume(issue_key)
            raise PublishError(str(exc)) from exc
        finally:
            statsd.timing(
                "jbi.publisher.publish.timer", (time.monotonic() - started) * 1000
            )

        statsd.incr("jbi.publisher.published.count")
        logger.info(
            "Published event on %s as message %s (delivery %s)",
            issue_key,
            message_id,
            event_id,
            extra={"message_id": message_id, "delivery_id": event_id},
        )
        return str(message_id)

    def _resume(self, issue_key: Optional[str]) -> None:
        """Un-pause the ordering key a failed publish paused."""
        if not issue_key:
            return
        try:
            self.client.resume_publish(self.topic_path, issue_key)
            statsd.incr("jbi.publisher.publish_resumed.count")
        except Exception:
            logger.exception(
                "Could not resume publishing for ordering key %s", issue_key
            )


@lru_cache(maxsize=1)
def _build_publisher(topic_id: str) -> JiraEventPublisher:
    return JiraEventPublisher(get_settings())


def get_publisher() -> Optional[JiraEventPublisher]:
    """Return the publisher, or `None` when publishing is not configured."""
    settings = get_settings()
    if not settings.pubsub_topic_id:
        return None
    return _build_publisher(settings.pubsub_topic_id)
