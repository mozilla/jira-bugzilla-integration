"""Duplicate suppression for at-least-once delivery.

Pub/Sub, and Jira Automation's own retries, deliver an event more than once.
Field writes are already safe to repeat (read-before-write), but a redelivered
*comment* event would post the comment to Bugzilla twice, and no field-level
check catches that.

The guard is a **claim/lease ledger**, not a "seen" flag. A bare flag has two
failure modes: it records an event as handled when the worker then fails (the
redelivery is dropped and the event is lost), and it forgets everything on
restart. Instead a worker *claims* an event before processing it and marks it
done afterwards:

* never seen          -> claimed, process it;
* done                -> duplicate, acknowledge without processing;
* claimed, lease live -> another worker is on it, try again later;
* claimed, lease over -> that worker is presumed dead, take over.

A failed attempt *releases* the claim so the redelivery is not mistaken for a
duplicate. `decide_claim` holds the whole decision and is shared by both
stores, so the in-memory store used in tests and local runs behaves exactly
like the Firestore one used in production.
"""

import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from enum import StrEnum, auto
from functools import lru_cache
from typing import Any, Callable, Optional, Protocol

import dockerflow.checks
from starlette.concurrency import run_in_threadpool

from jbi.environment import Settings, get_settings

logger = logging.getLogger(__name__)

PROCESSING = "processing"
DONE = "done"

Record = dict[str, Any]


class ClaimStatus(StrEnum):
    """The result of trying to claim an event."""

    CLAIMED = auto()
    TAKEN_OVER = auto()
    DUPLICATE = auto()
    IN_PROGRESS = auto()

    @property
    def should_process(self) -> bool:
        return self in (ClaimStatus.CLAIMED, ClaimStatus.TAKEN_OVER)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def decide_claim(
    existing: Optional[Record],
    now: datetime,
    lease: timedelta,
    ttl: timedelta,
    owner: Optional[str] = None,
) -> tuple[ClaimStatus, Optional[Record]]:
    """Decide what claiming an event means, and what to store.

    Returns the status and the record to write (`None` when nothing changes).
    Pure, so every branch is unit-tested without a database.
    """
    # A record past its expiry is as good as absent. Firestore's TTL sweep can
    # lag well behind `expire_at`, so the store must not rely on it deleting
    # on time.
    if existing is not None and existing["expire_at"] <= now:
        existing = None

    if existing is None:
        return ClaimStatus.CLAIMED, {
            "state": PROCESSING,
            "owner": owner,
            "attempts": 1,
            "first_seen": now,
            "updated_at": now,
            "lease_expires_at": now + lease,
            "expire_at": now + ttl,
        }

    if existing["state"] == DONE:
        return ClaimStatus.DUPLICATE, None

    if existing["lease_expires_at"] > now:
        return ClaimStatus.IN_PROGRESS, None

    return ClaimStatus.TAKEN_OVER, {
        **existing,
        "owner": owner,
        "attempts": existing.get("attempts", 1) + 1,
        "updated_at": now,
        "lease_expires_at": now + lease,
        "expire_at": now + ttl,
    }


def owns(record: Optional[Record], owner: Optional[str]) -> bool:
    """Whether `owner` still holds the claim on `record`.

    A worker whose lease ran out may have been taken over; settling the claim
    after that would delete or finish the new holder's claim, and the next
    redelivery would then be processed a second time. Records written before
    owners existed carry none and are treated as owned, which is the old
    behavior.
    """
    if record is None:
        return False
    holder = record.get("owner")
    return holder is None or owner is None or holder == owner


def finished(record: Record, now: datetime, ttl: timedelta) -> Record:
    """The record for an event that has been fully dealt with."""
    return {
        **record,
        "state": DONE,
        "updated_at": now,
        "lease_expires_at": None,
        "expire_at": now + ttl,
    }


class DedupStore(Protocol):
    """Where claims live."""

    async def claim(
        self, delivery_id: str, owner: Optional[str] = None
    ) -> ClaimStatus: ...

    async def complete(self, delivery_id: str, owner: Optional[str] = None) -> None:
        """Mark the event fully handled, if `owner` still holds the claim."""

    async def release(self, delivery_id: str, owner: Optional[str] = None) -> None:
        """Give the claim up so a redelivery can be processed, if `owner` holds it."""


class InMemoryDedupStore:
    """A bounded in-process ledger.

    Fine for tests, local runs and a single replica; forgotten on restart and
    not shared between processes. Production sets `FIRESTORE_DATABASE` for the
    shared store.
    """

    def __init__(
        self,
        lease_seconds: int = 660,
        ttl_days: int = 14,
        max_entries: int = 2048,
        clock: Callable[[], datetime] = utcnow,
    ):
        self._lease = timedelta(seconds=lease_seconds)
        self._ttl = timedelta(days=ttl_days)
        self._max = max_entries
        self._clock = clock
        self._records: OrderedDict[str, Record] = OrderedDict()
        # Each consumer message runs on its own thread and event loop.
        self._lock = threading.Lock()

    async def claim(self, delivery_id: str, owner: Optional[str] = None) -> ClaimStatus:
        with self._lock:
            status, record = decide_claim(
                self._records.get(delivery_id),
                self._clock(),
                self._lease,
                self._ttl,
                owner,
            )
            if record is not None:
                self._records[delivery_id] = record
                self._records.move_to_end(delivery_id)
                while len(self._records) > self._max:
                    self._records.popitem(last=False)
            return status

    async def complete(self, delivery_id: str, owner: Optional[str] = None) -> None:
        with self._lock:
            record = self._records.get(delivery_id)
            if owns(record, owner):
                self._records[delivery_id] = finished(record, self._clock(), self._ttl)  # type: ignore[arg-type]

    async def release(self, delivery_id: str, owner: Optional[str] = None) -> None:
        with self._lock:
            if owns(self._records.get(delivery_id), owner):
                self._records.pop(delivery_id, None)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class FirestoreDedupStore:
    """A ledger shared by every consumer replica and surviving restarts.

    One document per event in `collection`, keyed by delivery id. The claim
    runs in a transaction so two workers racing for the same event cannot both
    win. Documents carry `expire_at`; a Firestore TTL policy on that field
    (see the Terraform) deletes them, and `decide_claim` also honours it, so
    a late sweep never resurrects an old event.
    """

    def __init__(
        self,
        client: Any,
        lease_seconds: int,
        ttl_days: int,
        collection: str = "jbi_events",
        clock: Callable[[], datetime] = utcnow,
    ):
        self._client = client
        self._collection = client.collection(collection)
        self._lease = timedelta(seconds=lease_seconds)
        self._ttl = timedelta(days=ttl_days)
        self._clock = clock

    def _claim_sync(self, delivery_id: str, owner: Optional[str] = None) -> ClaimStatus:
        # Imported here, like the other Google clients: only processes that
        # use Firestore need it.
        from google.cloud import firestore

        ref = self._collection.document(delivery_id)

        @firestore.transactional
        def run(transaction) -> ClaimStatus:
            snapshot = ref.get(transaction=transaction)
            existing = snapshot.to_dict() if snapshot.exists else None
            status, record = decide_claim(
                existing, self._clock(), self._lease, self._ttl, owner
            )
            if record is not None:
                transaction.set(ref, record)
            return status

        status: ClaimStatus = run(self._client.transaction())
        return status

    def _settle_sync(self, delivery_id: str, owner: Optional[str], done: bool) -> None:
        """Finish or delete the claim, in a transaction, only if `owner` holds it."""
        from google.cloud import firestore

        ref = self._collection.document(delivery_id)

        @firestore.transactional
        def run(transaction) -> None:
            snapshot = ref.get(transaction=transaction)
            record = snapshot.to_dict() if snapshot.exists else None
            if not owns(record, owner):
                return
            if done:
                transaction.set(ref, finished(record, self._clock(), self._ttl))  # type: ignore[arg-type]
            else:
                transaction.delete(ref)

        run(self._client.transaction())

    async def claim(self, delivery_id: str, owner: Optional[str] = None) -> ClaimStatus:
        # The client is synchronous; keep it off the event loop.
        return await run_in_threadpool(self._claim_sync, delivery_id, owner)

    async def complete(self, delivery_id: str, owner: Optional[str] = None) -> None:
        await run_in_threadpool(self._settle_sync, delivery_id, owner, True)

    async def release(self, delivery_id: str, owner: Optional[str] = None) -> None:
        await run_in_threadpool(self._settle_sync, delivery_id, owner, False)

    def check_readable(self) -> list[dockerflow.checks.CheckMessage]:
        """Heartbeat check: the ledger is reachable and readable.

        Without it a Firestore outage first shows up as every event being
        retried, which is far harder to diagnose than a failing heartbeat.
        """
        try:
            self._collection.document("__healthcheck__").get()
        except Exception as exc:
            logger.exception(exc)
            return [
                dockerflow.checks.Error(
                    "dedup store (Firestore) cannot be read",
                    hint=f"check FIRESTORE_DATABASE and IAM (roles/datastore.user): {exc}",
                    id="dedup.firestore.read",
                )
            ]
        return []


def build_dedup_store(settings: Settings) -> DedupStore:
    """Firestore when configured, otherwise the in-process ledger."""
    if not settings.firestore_database:
        return InMemoryDedupStore(
            lease_seconds=settings.dedup_lease_seconds,
            ttl_days=settings.dedup_ttl_days,
        )

    from google.cloud import firestore

    client = firestore.Client(
        project=settings.firestore_project_id or settings.pubsub_project_id,
        database=settings.firestore_database,
    )
    return FirestoreDedupStore(
        client,
        lease_seconds=settings.dedup_lease_seconds,
        ttl_days=settings.dedup_ttl_days,
    )


@lru_cache(maxsize=1)
def get_dedup_store() -> DedupStore:
    """The process-wide ledger."""
    return build_dedup_store(get_settings())
