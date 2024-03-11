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
    another_request = webhook_request_factory.build()
    await queue.postpone(another_request)
    assert await queue.backend.size() == 2

    # Check what was stored.
    stored = await queue.backend.get(another_request.bug.id)
    assert len(stored) == 1
    assert stored[0].payload.bug.summary == another_request.bug.summary

    await queue.backend.clear()
    assert await queue.backend.size() == 0
