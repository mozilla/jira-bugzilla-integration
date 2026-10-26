"""One entry point into the core, for every transport (event injection).

Before this module each transport wired itself into the core its own way: the
Bugzilla webhook called `execute_or_queue`, the Jira webhook called
`execute_jira_event`, and the retry runner called `execute_action`. Adding a
fourth transport meant a fourth bespoke wiring, each with its own gating and
error handling.

Everything now converges on `ingest_event`, which takes a transport-agnostic
envelope and returns an **acknowledgement decision**. That return value is the
substantive part: HTTP only ever needed "did it work", while a message broker
needs to know whether to ack, redeliver, or dead-letter. In particular
`IGNORED` must ack -- an event JBI does not act on (out of scope, wrong
project, self-authored) is normal traffic, and nacking it would redeliver
forever.
"""

import logging
from collections import OrderedDict
from enum import StrEnum, auto
from typing import Optional, Union

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from jbi.bugzilla import models as bugzilla_models
from jbi.errors import IgnoreInvalidRequestError
from jbi.jira_inbound.handler import execute_jira_event
from jbi.jira_inbound.models import JiraWebhookRequest
from jbi.models import Actions
from jbi.queue import DeadLetterQueue
from jbi.runner import execute_action, execute_or_queue

logger = logging.getLogger(__name__)


class EventSource(StrEnum):
    """Which system an event describes."""

    BUGZILLA = auto()
    JIRA = auto()


class IngestOutcome(StrEnum):
    """What the transport should do about this event.

    HANDLED / IGNORED -> acknowledge; the event is finished with.
    RETRY             -> redeliver later; a transient failure.
    PERMANENT_FAILURE -> acknowledge but do not retry; redelivery cannot help.
    """

    HANDLED = auto()
    IGNORED = auto()
    RETRY = auto()
    PERMANENT_FAILURE = auto()


class InboundEvent(BaseModel, frozen=True, arbitrary_types_allowed=True):
    """A transport-agnostic event envelope."""

    source: EventSource
    payload: Union[bugzilla_models.WebhookRequest, JiraWebhookRequest]
    # Broker-assigned id, used for duplicate suppression. `None` for HTTP.
    message_id: Optional[str] = None
    delivery_attempt: int = 1
    rid: Optional[str] = None


class IngestResult(BaseModel):
    """The outcome of ingesting one event."""

    outcome: IngestOutcome
    reason: Optional[str] = None
    details: Optional[dict] = None

    @property
    def should_acknowledge(self) -> bool:
        """Whether the transport may consider this event delivered."""
        return self.outcome != IngestOutcome.RETRY


# --- Duplicate suppression --------------------------------------------------
#
# At-least-once delivery makes duplicates normal rather than exceptional. Most
# of the pipeline is already idempotent -- Invariant A stops duplicate Jira
# issues, read-before-write stops duplicate BMO field writes -- but a
# redelivered *comment* event would post the text twice, which no field-level
# check catches.
#
# This is a bounded, in-process cache: cheap, and enough for the redelivery
# bursts a broker actually produces (seconds apart, same instance). It is
# explicitly **not** a distributed guarantee -- two instances do not share it,
# and a restart forgets everything. A shared store (Redis) is the real answer
# and is deliberately deferred rather than half-built here.

_SEEN_MESSAGE_IDS: OrderedDict[str, None] = OrderedDict()
_SEEN_MAX = 2048


def already_delivered(message_id: Optional[str]) -> bool:
    """Record a message id and report whether it had been seen before."""
    if not message_id:
        return False
    if message_id in _SEEN_MESSAGE_IDS:
        return True
    _SEEN_MESSAGE_IDS[message_id] = None
    while len(_SEEN_MESSAGE_IDS) > _SEEN_MAX:
        _SEEN_MESSAGE_IDS.popitem(last=False)
    return False


def reset_delivery_cache() -> None:
    """Forget every recorded message id (used by tests)."""
    _SEEN_MESSAGE_IDS.clear()


# --- Ingestion --------------------------------------------------------------


async def ingest_event(
    event: InboundEvent,
    actions: Actions,
    queue: Optional[DeadLetterQueue] = None,
) -> IngestResult:
    """Run one inbound event through the core and report what to do next.

    `queue` selects who owns retries. The Bugzilla webhook passes the
    dead-letter queue, preserving today's behavior. A broker transport passes
    `None`, because the subscription's own retry and dead-letter topic own
    that -- two retry mechanisms stacked on each other would multiply
    redeliveries.
    """
    if already_delivered(event.message_id):
        logger.info(
            "Duplicate delivery of message %s ignored",
            event.message_id,
            extra={"source": str(event.source), "message_id": event.message_id},
        )
        return IngestResult(outcome=IngestOutcome.IGNORED, reason="duplicate delivery")

    if event.source == EventSource.BUGZILLA:
        return await _ingest_bugzilla(event, actions, queue)
    return await _ingest_jira(event, actions)


async def _ingest_bugzilla(
    event: InboundEvent, actions: Actions, queue: Optional[DeadLetterQueue]
) -> IngestResult:
    payload = event.payload
    assert isinstance(payload, bugzilla_models.WebhookRequest)

    if queue is not None:
        # Existing behavior, unchanged: the dead-letter queue absorbs failures
        # and `execute_or_queue` reports them as a status string.
        response = await execute_or_queue(payload, queue, actions)
        status = response.get("status") if isinstance(response, dict) else None
        if status == "invalid":
            return IngestResult(
                outcome=IngestOutcome.IGNORED, reason=response.get("error")
            )
        if status == "failed":
            # Already captured in the queue; retrying at the transport level
            # would duplicate that work.
            return IngestResult(
                outcome=IngestOutcome.PERMANENT_FAILURE,
                reason=response.get("error"),
                details=response,
            )
        return IngestResult(outcome=IngestOutcome.HANDLED, details=response)

    try:
        # Blocking I/O (Bugzilla/Jira HTTP, pandoc) must not run on the event
        # loop: this process has a single loop and no other workers, so a slow
        # event would freeze the pod including its own health check. Same
        # reasoning as `execute_or_queue`.
        details = await run_in_threadpool(execute_action, payload, actions)
    except IgnoreInvalidRequestError as exc:
        return IngestResult(outcome=IngestOutcome.IGNORED, reason=str(exc))
    except Exception as exc:
        logger.exception(
            "Failed to ingest Bugzilla event for Bug %s",
            payload.bug.id,
            extra={"message_id": event.message_id},
        )
        return IngestResult(outcome=IngestOutcome.RETRY, reason=str(exc))
    return IngestResult(outcome=IngestOutcome.HANDLED, details=details)


async def _ingest_jira(event: InboundEvent, actions: Actions) -> IngestResult:
    payload = event.payload
    assert isinstance(payload, JiraWebhookRequest)

    try:
        # Threadpool for the same reason as the forward path: the reverse
        # pipeline makes several blocking Jira and Bugzilla calls per event.
        details = await run_in_threadpool(execute_jira_event, payload, actions)
    except IgnoreInvalidRequestError as exc:
        return IngestResult(outcome=IngestOutcome.IGNORED, reason=str(exc))
    except Exception as exc:
        logger.exception(
            "Failed to ingest Jira event for issue %s",
            payload.issue.key if payload.issue else "?",
            extra={"message_id": event.message_id},
        )
        return IngestResult(outcome=IngestOutcome.RETRY, reason=str(exc))
    return IngestResult(outcome=IngestOutcome.HANDLED, details=details)
