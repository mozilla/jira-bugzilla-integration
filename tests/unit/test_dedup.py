"""Tests for the claim/lease ledger.

`decide_claim` carries the whole decision and is shared by both stores, so it
is tested directly; the in-memory store is then tested for its own bookkeeping.
The Firestore store is exercised against the emulator when one is available
(`FIRESTORE_EMULATOR_HOST`) and skipped otherwise.
"""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from jbi.dedup import (
    DONE,
    PROCESSING,
    ClaimStatus,
    FirestoreDedupStore,
    InMemoryDedupStore,
    decide_claim,
    finished,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
LEASE = timedelta(seconds=600)
TTL = timedelta(days=14)


def record(state=PROCESSING, lease_left=300, expires_in=3600, attempts=1):
    return {
        "state": state,
        "attempts": attempts,
        "first_seen": NOW - timedelta(seconds=10),
        "updated_at": NOW - timedelta(seconds=10),
        "lease_expires_at": NOW + timedelta(seconds=lease_left),
        "expire_at": NOW + timedelta(seconds=expires_in),
    }


# --- decide_claim -----------------------------------------------------------


def test_an_unseen_event_is_claimed():
    status, stored = decide_claim(None, NOW, LEASE, TTL)

    assert status == ClaimStatus.CLAIMED
    assert stored["state"] == PROCESSING
    assert stored["attempts"] == 1
    assert stored["lease_expires_at"] == NOW + LEASE
    assert stored["expire_at"] == NOW + TTL


def test_a_finished_event_is_a_duplicate_and_nothing_is_written():
    status, stored = decide_claim(record(state=DONE), NOW, LEASE, TTL)

    assert status == ClaimStatus.DUPLICATE
    assert stored is None


def test_an_event_another_worker_is_processing_is_left_alone():
    status, stored = decide_claim(record(lease_left=1), NOW, LEASE, TTL)

    assert status == ClaimStatus.IN_PROGRESS
    assert stored is None


def test_an_event_whose_lease_ran_out_is_taken_over():
    """The previous worker is presumed dead; without this its event would sit
    unprocessed forever."""
    status, stored = decide_claim(record(lease_left=-1, attempts=2), NOW, LEASE, TTL)

    assert status == ClaimStatus.TAKEN_OVER
    assert stored["attempts"] == 3
    assert stored["lease_expires_at"] == NOW + LEASE


def test_an_expired_record_counts_as_absent_even_if_the_ttl_sweep_is_late():
    status, stored = decide_claim(record(state=DONE, expires_in=-1), NOW, LEASE, TTL)

    assert status == ClaimStatus.CLAIMED
    assert stored["attempts"] == 1


def test_finishing_clears_the_lease_and_extends_the_expiry():
    done = finished(record(), NOW, TTL)

    assert done["state"] == DONE
    assert done["lease_expires_at"] is None
    assert done["expire_at"] == NOW + TTL


@pytest.mark.parametrize(
    "status, expected",
    [
        (ClaimStatus.CLAIMED, True),
        (ClaimStatus.TAKEN_OVER, True),
        (ClaimStatus.DUPLICATE, False),
        (ClaimStatus.IN_PROGRESS, False),
    ],
)
def test_only_claimed_and_taken_over_events_are_processed(status, expected):
    assert status.should_process is expected


# --- the in-memory store ----------------------------------------------------


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return InMemoryDedupStore(
        lease_seconds=600, ttl_days=14, max_entries=3, clock=clock
    )


@pytest.mark.asyncio
async def test_claim_then_complete_makes_later_deliveries_duplicates(store):
    assert await store.claim("e1") == ClaimStatus.CLAIMED
    await store.complete("e1")

    assert await store.claim("e1") == ClaimStatus.DUPLICATE


@pytest.mark.asyncio
async def test_a_second_claim_while_processing_is_in_progress(store):
    await store.claim("e1")

    assert await store.claim("e1") == ClaimStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_a_released_claim_can_be_claimed_again(store):
    """A failed attempt must not make its own redelivery look like a
    duplicate -- that would turn a transient error into a lost event."""
    await store.claim("e1")
    await store.release("e1")

    assert await store.claim("e1") == ClaimStatus.CLAIMED


@pytest.mark.asyncio
async def test_a_crashed_workers_event_is_taken_over_after_the_lease(store, clock):
    await store.claim("e1")
    clock.advance(seconds=601)

    assert await store.claim("e1") == ClaimStatus.TAKEN_OVER


@pytest.mark.asyncio
async def test_completed_events_are_forgotten_after_the_ttl(store, clock):
    await store.claim("e1")
    await store.complete("e1")
    clock.advance(days=15)

    assert await store.claim("e1") == ClaimStatus.CLAIMED


@pytest.mark.asyncio
async def test_the_store_is_bounded(store):
    for i in range(10):
        await store.claim(f"e{i}")

    assert len(store._records) == 3
    # The oldest were evicted, the documented limit of an in-process store.
    assert await store.claim("e0") == ClaimStatus.CLAIMED


@pytest.mark.asyncio
async def test_completing_or_releasing_an_unknown_event_is_harmless(store):
    await store.complete("nope")
    await store.release("nope")


def test_concurrent_claims_have_exactly_one_winner():
    """Consumer messages run on separate threads, each with its own loop."""
    import asyncio

    shared = InMemoryDedupStore()
    results = []

    def worker():
        results.append(asyncio.run(shared.claim("race")))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(ClaimStatus.CLAIMED) == 1
    assert results.count(ClaimStatus.IN_PROGRESS) == 15


# --- the Firestore store (emulator only) ------------------------------------

emulator = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator (set FIRESTORE_EMULATOR_HOST)",
)


@pytest.fixture
def firestore_store():
    from google.cloud import firestore

    client = firestore.Client(project="jbi-test")
    return FirestoreDedupStore(
        client,
        lease_seconds=600,
        ttl_days=14,
        collection=f"jbi_events_test_{uuid.uuid4().hex}",
    )


@emulator
@pytest.mark.asyncio
async def test_firestore_claim_complete_cycle(firestore_store):
    assert await firestore_store.claim("e1") == ClaimStatus.CLAIMED
    assert await firestore_store.claim("e1") == ClaimStatus.IN_PROGRESS
    await firestore_store.complete("e1")
    assert await firestore_store.claim("e1") == ClaimStatus.DUPLICATE


@emulator
@pytest.mark.asyncio
async def test_firestore_release_allows_a_new_claim(firestore_store):
    await firestore_store.claim("e1")
    await firestore_store.release("e1")

    assert await firestore_store.claim("e1") == ClaimStatus.CLAIMED


@emulator
@pytest.mark.asyncio
async def test_firestore_concurrent_claims_have_one_winner(firestore_store):
    import asyncio

    results = await asyncio.gather(*(firestore_store.claim("race") for _ in range(8)))

    assert results.count(ClaimStatus.CLAIMED) == 1


# --- Health check -----------------------------------------------------------


def _store_with_client(client):
    return FirestoreDedupStore(client, lease_seconds=600, ttl_days=14)


def test_health_check_passes_when_the_ledger_is_readable():
    from unittest import mock

    assert _store_with_client(mock.MagicMock()).check_readable() == []


def test_health_check_reports_an_unreachable_ledger():
    from unittest import mock

    client = mock.MagicMock()
    client.collection.return_value.document.return_value.get.side_effect = RuntimeError(
        "403 permission denied"
    )

    (error,) = _store_with_client(client).check_readable()

    assert error.id == "dedup.firestore.read"
    assert "permission denied" in error.hint
