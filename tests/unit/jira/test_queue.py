from datetime import datetime, timedelta

import pytest
from pydantic import HttpUrl

from jbi.queue import (
    DeadLetterQueue,
    FileBackend,
    InvalidQueueDSNError,
    QueueBackend,
    QueueItemRetrievalError,
)


@pytest.fixture
def backend(tmp_path):
    return FileBackend(tmp_path)


@pytest.fixture
def queue(tmp_path):
    return DeadLetterQueue("file://" + str(tmp_path))


@pytest.mark.parametrize(
    "dsn", ["memory://", "http://www.example.com", HttpUrl("http://www.example.com")]
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
    assert await backend.size() == 1

    await backend.remove(item.payload.bug.id, item.identifier)
    assert await backend.size() == 0


@pytest.mark.asyncio
async def test_clear(backend: QueueBackend, queue_item_factory):
    item_1 = queue_item_factory(payload__bug__id=123)
    item_2 = queue_item_factory(payload__bug__id=456)

    await backend.put(item_1)
    await backend.put(item_2)
    assert await backend.size() == 2

    await backend.clear()
    assert await backend.size() == 0


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

    items = [item async for item in backend.get(item_1.payload.bug.id)]
    assert list(items) == [item_1, item_2, item_3, item_4]


@pytest.mark.asyncio
async def test_list_all(backend: QueueBackend, queue_item_factory):
    for bug_id in (123, 123, 456, 456):
        await backend.put(queue_item_factory(payload__bug__id=bug_id))

    all_items = await backend.list_all()
    assert len(all_items) == 2

    for items in all_items.values():
        assert len(items) == 2


@pytest.mark.asyncio
async def test_list_by_bug(backend: QueueBackend, queue_item_factory):
    item_1 = queue_item_factory(payload__bug__id=123)
    item_2 = queue_item_factory(payload__bug__id=456)

    await backend.put(item_1)
    await backend.put(item_2)
    [identifier] = await backend.list(bug_id=123)
    assert identifier == item_1.identifier


@pytest.mark.asyncio
async def test_list_ordering(backend: QueueBackend, queue_item_factory):
    now = datetime.now()
    item_1 = queue_item_factory(payload__event__time=now + timedelta(minutes=1))
    item_2 = queue_item_factory(payload__event__time=now + timedelta(minutes=2))
    item_3 = queue_item_factory(payload__event__time=now + timedelta(minutes=3))
    item_4 = queue_item_factory(payload__event__time=now + timedelta(minutes=4))

    await backend.put(item_2)
    await backend.put(item_1)
    await backend.put(item_3)
    await backend.put(item_4)

    item_metadata = await backend.list(bug_id=item_1.payload.bug.id)
    exptected_id_order = [item.identifier for item in [item_1, item_2, item_3, item_4]]
    assert exptected_id_order == item_metadata


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
    assert [item async for item in items[123]] == [item_1, item_3]
    assert [item async for item in items[456]] == [item_2, item_4]


@pytest.mark.asyncio
async def test_get_all_invalid_json(backend: QueueBackend, queue_item_factory):
    item_1 = queue_item_factory()
    await backend.put(item_1)

    corrupt_file_dir = backend.location / "999"
    corrupt_file_dir.mkdir()

    corrupt_file_path = corrupt_file_dir / "xxx.json"
    corrupt_file_path.write_text("BOOM")

    items = await backend.get_all()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_get_all_payload_doesnt_match_schema(
    backend: QueueBackend, queue_item_factory
):
    item_1 = queue_item_factory()
    await backend.put(item_1)

    # this is invalid, as whiteboard should be a string
    item_2 = queue_item_factory.build(
        payload__bug__id=999, payload__bug__whiteboard=False
    )
    await backend.put(item_2)

    items = await backend.get_all()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_get_invalid_json(backend: QueueBackend, queue_item_factory):
    corrupt_file_dir = backend.location / "999"
    corrupt_file_dir.mkdir()
    corrupt_file_path = corrupt_file_dir / "xxx.json"
    corrupt_file_path.write_text("BOOM")

    items = backend.get(999)

    with pytest.raises(QueueItemRetrievalError):
        await anext(items)


@pytest.mark.asyncio
async def test_get_payload_doesnt_match_schema(
    backend: QueueBackend, queue_item_factory
):
    # this is invalid, as whiteboard should be a string
    item = queue_item_factory.build(
        payload__bug__id=999, payload__bug__whiteboard=False
    )
    await backend.put(item)

    items = backend.get(999)

    with pytest.raises(QueueItemRetrievalError):
        await anext(items)


@pytest.mark.asyncio
async def test_postpone(queue: DeadLetterQueue, webhook_request_factory):
    webhook_payload = webhook_request_factory()
    await queue.postpone(webhook_payload)

    [item] = [_ async for _ in queue.backend.get(webhook_payload.bug.id)]
    assert item.payload == webhook_payload


@pytest.mark.asyncio
async def test_track_failed(queue: DeadLetterQueue, webhook_request_factory):
    webhook_payload = webhook_request_factory()
    exc = Exception("boom")

    await queue.track_failed(webhook_payload, exc)
    [item] = [_ async for _ in queue.backend.get(webhook_payload.bug.id)]
    assert item.payload == webhook_payload

    assert item.payload == webhook_payload
    assert item.error.description == str(exc)


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
async def test_size(backend, queue_item_factory):
    item = queue_item_factory(payload__bug__id=1)
    another_item = queue_item_factory(payload__bug__id=2)

    await backend.put(item)
    await backend.put(another_item)

    assert await backend.size() == 2
    assert await backend.size(bug_id=1) == 1


@pytest.mark.asyncio
async def test_size_empty(backend, queue_item_factory):
    assert await backend.size() == 0
    assert await backend.size(bug_id=999) == 0


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
    assert len(items) == 3
    bug_1_items = [item async for item in items[1]]
    assert bug_1_items[0].payload.event.time < bug_1_items[1].payload.event.time


@pytest.mark.asyncio
async def test_done(queue: DeadLetterQueue, queue_item_factory):
    item = queue_item_factory()

    await queue.backend.put(item)
    assert await queue.backend.size() == 1

    await queue.done(item)
    assert await queue.backend.size() == 0
