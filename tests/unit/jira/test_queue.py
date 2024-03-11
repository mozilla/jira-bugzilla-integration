import tempfile

import pytest

from jbi.queue import DeadLetterQueue


@pytest.mark.parametrize(
    "dsn",
    [("file://" + tempfile.mkdtemp()), ("memory://")],
)
@pytest.mark.asyncio
async def test_basic_queue_features(dsn, webhook_request_factory):
    queue = DeadLetterQueue(dsn)
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
