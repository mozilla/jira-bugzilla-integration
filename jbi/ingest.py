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
import time
from enum import StrEnum, auto
from typing import Optional, Union

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from statsd.defaults.env import statsd

from jbi.bugzilla import models as bugzilla_models
from jbi.dedup import ClaimStatus, DedupStore, get_dedup_store
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
# `jbi.dedup` holds the guard: a claim/lease ledger, shared through Firestore
# in production and in-process otherwise. See that module for why it records
# a claim rather than a bare "seen" flag.


def reset_delivery_cache() -> None:
    """Forget every recorded event (used by tests)."""
    from jbi import dedup

    dedup.get_dedup_store.cache_clear()


# --- Ingestion --------------------------------------------------------------


async def ingest_event(
    event: InboundEvent,
    actions: Actions,
    queue: Optional[DeadLetterQueue] = None,
    dedup: Optional[DedupStore] = None,
) -> IngestResult:
    """Run one inbound event through the core and report what to do next.

    `queue` selects who owns retries. The Bugzilla webhook passes the
    dead-letter queue, preserving today's behavior. A broker transport passes
    `None`, because the subscription's own retry and dead-letter topic own
    that -- two retry mechanisms stacked on each other would multiply
    redeliveries.

    Events that carry an id (everything from a broker) are claimed in the
    dedup ledger first, and the claim is settled afterwards: finished events
    are marked done, failed ones released so the redelivery is processed
    rather than mistaken for a duplicate.
    """
    delivery_id = event.message_id
    if not delivery_id:
        return await _dispatch(event, actions, queue)

    store = dedup or get_dedup_store()
    started = time.monotonic()
    try:
        status = await store.claim(delivery_id)
    except Exception as exc:
        # Fail closed. Processing without the guard is exactly what produces
        # duplicate comments; a transient store outage is better absorbed by
        # redelivery (and alerted on) than by writing twice.
        statsd.incr("jbi.dedup.store_error.count")
        logger.exception(
            "Dedup store unavailable for message %s",
            delivery_id,
            extra={"source": str(event.source), "message_id": delivery_id},
        )
        return IngestResult(
            outcome=IngestOutcome.RETRY, reason=f"dedup store unavailable: {exc}"
        )
    finally:
        statsd.timing("jbi.dedup.claim.timer", (time.monotonic() - started) * 1000)

    statsd.incr(f"jbi.dedup.{_CLAIM_METRIC[status]}.count")
    if status == ClaimStatus.DUPLICATE:
        logger.info(
            "Duplicate delivery of message %s ignored",
            delivery_id,
            extra={"source": str(event.source), "message_id": delivery_id},
        )
        return IngestResult(outcome=IngestOutcome.IGNORED, reason="duplicate delivery")
    if status == ClaimStatus.IN_PROGRESS:
        # Another worker holds a live claim. Ask for redelivery rather than
        # acknowledging: if that worker dies, the lease expires and a later
        # delivery takes the event over.
        return IngestResult(
            outcome=IngestOutcome.RETRY, reason="another worker is processing it"
        )

    try:
        result = await _dispatch(event, actions, queue)
    except BaseException:
        await _settle(store, delivery_id, done=False)
        raise
    await _settle(store, delivery_id, done=result.outcome != IngestOutcome.RETRY)
    return result


_CLAIM_METRIC = {
    ClaimStatus.CLAIMED: "claimed",
    ClaimStatus.TAKEN_OVER: "takeover",
    ClaimStatus.DUPLICATE: "duplicate",
    ClaimStatus.IN_PROGRESS: "in_progress",
}


async def _settle(store: DedupStore, delivery_id: str, *, done: bool) -> None:
    """Mark a claim finished or give it up, without masking the real outcome.

    If this fails the lease simply runs out and the event is taken over, so
    the failure is logged and counted rather than raised over a result that
    already happened.
    """
    try:
        await (store.complete(delivery_id) if done else store.release(delivery_id))
    except Exception:
        statsd.incr("jbi.dedup.store_error.count")
        logger.exception("Could not settle dedup claim for message %s", delivery_id)


async def _dispatch(
    event: InboundEvent, actions: Actions, queue: Optional[DeadLetterQueue]
) -> IngestResult:
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
