from datetime import datetime, timedelta

import pytest
from pydantic import HttpUrl

from jbi.queue import (
    DeadLetterQueue,
    FileBackend,
    InvalidQueueDSNError,
    MemoryBackend,
    QueueBackend,
)


@pytest.fixture
def _memory_backend():
    return MemoryBackend()


@pytest.fixture
def _file_backend(tmp_path):
    return FileBackend(tmp_path)


@pytest.fixture(params=["_memory_backend", "_file_backend"])
def backend(request):
    backend = request.getfixturevalue(request.param)
    return backend


@pytest.fixture
def _memory_dlq():
    return DeadLetterQueue("memory://")


@pytest.fixture
def _filesystem_dlq(tmp_path):
    return DeadLetterQueue("file://" + str(tmp_path))


@pytest.fixture(params=["_memory_dlq", "_filesystem_dlq"])
def queue(request):
    queue = request.getfixturevalue(request.param)
    return queue


@pytest.mark.parametrize(
    "dsn", ["http://www.example.com", HttpUrl("http://www.example.com")]
)
def test_invalid_queue_url(dsn):
    with pytest.raises(InvalidQueueDSNError):
        DeadLetterQueue(dsn)


def test_ping(backend: QueueBackend):
    assert backend.ping() is True


def test_filebackend_ping_fails(caplog, tmp_path):
    tmp_path.chmod(0o400)  # set to readonly
    backend = FileBackend(tmp_path)
    assert backend.ping() is False


@pytest.mark.asyncio
async def test_remove_last_item(backend: QueueBackend, queue_item_factory):
    """When we remove the last item for a bug, we also remove it's key from the
    backend"""

    item = queue_item_factory()

    await backend.put(item)
    assert backend.size == 1
    assert len(await backend.get_all()) == 1

    await backend.remove(item.payload.bug.id, item.identifier)
    assert backend.size == 0
    assert len(await backend.get_all()) == 0


@pytest.mark.asyncio
async def test_clear(backend: QueueBackend, queue_item_factory):
    item_1 = queue_item_factory(payload__bug__id=123)
    item_2 = queue_item_factory(payload__bug__id=456)

    await backend.put(item_1)
    await backend.put(item_2)
    assert backend.size == 2
    assert len(await backend.get_all()) == 2

    await backend.clear()
    assert backend.size == 0
    assert len(await backend.get_all()) == 0


@pytest.mark.asyncio
async def test_put_maintains_sorted_order(backend: QueueBackend, queue_item_factory):
    now = datetime.now()
    item_1 = queue_item_factory(payload__event__time=now + timedelta(minutes=1))
    item_2 = queue_item_factory(payload__event__time=now + timedelta(minutes=2))
    item_3 = queue_item_factory(payload__event__time=now + timedelta(minutes=3))
    item_4 = queue_item_factory(payload__event__time=now + timedelta(minutes=4))

    await backend.put(item_2)
    await backend.put(item_1)
    await backend.put(item_3)
    await backend.put(item_4)

    items = await backend.get(item_1.payload.bug.id)
    assert items == [item_1, item_2, item_3, item_4]


@pytest.mark.asyncio
async def test_get_all(backend: QueueBackend, queue_item_factory):
    now = datetime.now()
    item_1 = queue_item_factory(
        payload__bug__id=123, payload__event__time=now + timedelta(minutes=1)
    )
    item_2 = queue_item_factory(
        payload__bug__id=456, payload__event__time=now + timedelta(minutes=2)
    )
    item_3 = queue_item_factory(
        payload__bug__id=123, payload__event__time=now + timedelta(minutes=3)
    )
    item_4 = queue_item_factory(
        payload__bug__id=456, payload__event__time=now + timedelta(minutes=4)
    )

    await backend.put(item_3)
    await backend.put(item_4)
    await backend.put(item_1)
    await backend.put(item_2)

    items = await backend.get_all()
    assert len(items) == 2
    assert items[123] == [item_1, item_3]
    assert items[456] == [item_2, item_4]


@pytest.mark.asyncio
async def test_postpone(queue: DeadLetterQueue, webhook_request_factory):
    webhook_payload = webhook_request_factory()

    await queue.postpone(webhook_payload)

    [event] = await queue.backend.get(webhook_payload.bug.id)
    assert event.payload == webhook_payload


@pytest.mark.asyncio
async def test_track_failed(queue: DeadLetterQueue, webhook_request_factory):
    webhook_payload = webhook_request_factory()
    exc = Exception("boom")

    await queue.track_failed(webhook_payload, exc)
    [event] = await queue.backend.get(webhook_payload.bug.id)

    assert event.payload == webhook_payload
    assert event.error.description == str(exc)


@pytest.mark.asyncio
async def test_is_blocked(
    queue: DeadLetterQueue, queue_item_factory, webhook_request_factory
):
    blocked_payload = webhook_request_factory(bug__id=123)
    item = queue_item_factory(payload=blocked_payload)
    await queue.backend.put(item)

    assert await queue.is_blocked(blocked_payload) is True

    another_payload = webhook_request_factory(bug__id=456)
    assert await queue.is_blocked(another_payload) is False


@pytest.mark.asyncio
async def test_retrieve(queue: DeadLetterQueue, queue_item_factory):
    bug_ids = (1, 2, 1, 3)
    now = datetime.now()

    for idx, bug_id in enumerate(bug_ids):
        timestamp = now + timedelta(minutes=idx)
        await queue.backend.put(
            queue_item_factory(
                timestamp=timestamp,
                payload__event__time=timestamp,
                payload__bug__id=bug_id,
            )
        )

    items = await queue.retrieve()
    assert [item.payload.bug.id for item in items] == [1, 1, 2, 3]


@pytest.mark.asyncio
async def test_done(queue: DeadLetterQueue, queue_item_factory):
    item = queue_item_factory()

    await queue.backend.put(item)
    assert queue.backend.size == 1

    await queue.done(item)
    assert queue.backend.size == 0


@pytest.mark.asyncio
async def test_basic_queue_features(queue, webhook_request_factory):
    assert queue.backend.size == 0

    # Track a failure.
    request = webhook_request_factory(bug__id=314)
    await queue.track_failed(request, ValueError("bam!"))
    assert queue.backend.size == 1

    request_same_bug = webhook_request_factory(bug__id=request.bug.id)
    assert await queue.is_blocked(request_same_bug)

    request_other_bug = webhook_request_factory(bug__id=42)
    assert not await queue.is_blocked(request_other_bug)

    # Postpone a request.
    bug_id = 777
    another_request = webhook_request_factory(bug__id=bug_id)
    await queue.postpone(another_request)
    assert queue.backend.size == 2

    # Store an old event request.
    old_request = webhook_request_factory(
        bug__id=bug_id, event__time="1982-05-05T13:20:17.495000+00:00"
    )
    await queue.track_failed(old_request, ValueError("blah"))

    # Check what was stored for this bug.
    stored = await queue.backend.get(bug_id)
    assert len(stored) == 2
    assert stored[0].payload.bug.summary == another_request.bug.summary

    # Oldest event brought `bug_id` items first.
    all_items = await queue.retrieve()
    assert [i.payload.bug.id for i in all_items] == [bug_id, bug_id, request.bug.id]
    assert all_items[0].payload.event.time < all_items[1].payload.event.time

    # Mark one as done
    await queue.done(all_items[0])
    assert queue.backend.size == 2

    # Clear
    await queue.backend.clear()
    assert queue.backend.size == 0
