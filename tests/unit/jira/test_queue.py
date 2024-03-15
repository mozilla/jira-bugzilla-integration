from datetime import datetime, timedelta

import pytest

from jbi.queue import DeadLetterQueue

pytestmark = [pytest.mark.asyncio]


@pytest.fixture
def memory_dlq():
    return DeadLetterQueue("memory://")


@pytest.fixture
def filesystem_dlq(tmp_path):
    return DeadLetterQueue("file://" + str(tmp_path))


@pytest.fixture(params=["memory_dlq", "filesystem_dlq"])
def queue(request):
    queue = request.getfixturevalue(request.param)
    return queue


async def test_postpone(queue: DeadLetterQueue, webhook_request_factory):
    webhook_payload = webhook_request_factory()

    await queue.postpone(webhook_payload)

    [event] = await queue.backend.get(webhook_payload.bug.id)
    assert event.payload == webhook_payload


async def test_track_failed(queue: DeadLetterQueue, webhook_request_factory):
    webhook_payload = webhook_request_factory()
    exc = Exception("boom")

    await queue.track_failed(webhook_payload, exc)
    [event] = await queue.backend.get(webhook_payload.bug.id)

    assert event.payload == webhook_payload
    assert event.error.description == str(exc)


async def test_is_blocked(
    queue: DeadLetterQueue, queue_item_factory, webhook_request_factory
):
    blocked_payload = webhook_request_factory(bug__id=123)
    item = queue_item_factory(payload=blocked_payload)
    await queue.backend.put(item)

    assert await queue.is_blocked(blocked_payload) is True

    another_payload = webhook_request_factory(bug__id=456)
    assert await queue.is_blocked(another_payload) is False


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


async def test_done(queue: DeadLetterQueue, queue_item_factory):
    item = queue_item_factory()

    await queue.backend.put(item)
    assert await queue.backend.size() == 1

    await queue.done(item)
    assert await queue.backend.size() == 0


async def test_basic_queue_features(queue, webhook_request_factory):
    assert await queue.backend.size() == 0

    # Track a failure.
    request = webhook_request_factory.build(bug__id=314)
    await queue.track_failed(request, ValueError("bam!"))
    assert await queue.backend.size() == 1

    request_same_bug = webhook_request_factory.build(bug__id=request.bug.id)
    assert await queue.is_blocked(request_same_bug)

    request_other_bug = webhook_request_factory.build(bug__id=42)
    assert not await queue.is_blocked(request_other_bug)

    # Postpone a request.
    bug_id = 777
    another_request = webhook_request_factory.build(bug__id=bug_id)
    await queue.postpone(another_request)
    assert await queue.backend.size() == 2

    # Store an old event request.
    old_request = webhook_request_factory.build(
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
    assert await queue.backend.size() == 2

    # Clear
    await queue.backend.clear()
    assert await queue.backend.size() == 0
