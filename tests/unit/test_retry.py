from datetime import datetime, timedelta
from os import getenv
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from jbi.queue import QueueItem, get_dl_queue
from jbi.retry import retry_failed

RETRY_TIMEOUT_DAYS = getenv("RETRY_TIMEOUT_DAYS", 7)


def add_iter(obj):
    mock = MagicMock()
    mock.__aiter__.return_value = obj
    return mock


def iter_error():
    mock = MagicMock()
    mock.__aiter__.return_value = None
    mock.__aiter__.side_effect = Exception("Throwing an exception")
    return mock


@pytest.mark.asyncio
async def test_retry_empty_list(caplog):
    retrieve = AsyncMock(return_value={})
    get_dl_queue().retrieve = retrieve

    metrics = await retry_failed()
    retrieve.assert_called_once()
    assert len(caplog.messages) == 0
    assert metrics == {
        "bug_count": 0,
        "events_processed": 0,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_success(caplog, queue_item_factory):
    exec_mock = MagicMock()
    queue = get_dl_queue()
    queue.retrieve = AsyncMock(
        return_value={
            1: add_iter(
                [
                    queue_item_factory(
                        payload__bug__id=1, payload__event__time=datetime.now()
                    )
                ]
            )
        }
    )
    queue.done = AsyncMock()

    metrics = await retry_failed(item_executor=exec_mock)
    assert len(caplog.messages) == 0
    queue.retrieve.assert_called_once()  # no logs should have been generated
    queue.done.assert_called_once()  # item should be marked as complete
    exec_mock.assert_called_once()  # item should have been processed
    assert metrics == {
        "bug_count": 1,
        "events_processed": 1,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_fail_and_skip(caplog, queue_item_factory):
    queue = get_dl_queue()
    queue.retrieve = AsyncMock(
        return_value={
            1: add_iter(
                [
                    queue_item_factory(
                        payload__bug__id=1, payload__event__time=datetime.now()
                    ),
                    queue_item_factory(
                        payload__bug__id=1, payload__event__time=datetime.now()
                    ),
                ]
            )
        }
    )

    exec_mock = MagicMock()
    exec_mock.side_effect = Exception("Throwing an exception")
    queue.done = AsyncMock()
    queue.list = AsyncMock(return_value=["a", "b"])

    metrics = await retry_failed(item_executor=exec_mock)
    queue.retrieve.assert_called_once()
    queue.done.assert_not_called()  # no items should have been marked as done
    assert caplog.text.count("failed to reprocess event") == 1
    assert caplog.text.count("skipping events") == 1
    assert caplog.text.count("removing expired event") == 0
    exec_mock.assert_called_once()  # only one item should have been attempted to be processed
    assert metrics == {
        "bug_count": 1,
        "events_processed": 0,
        "events_skipped": 1,
        "events_failed": 1,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_remove_expired(caplog, queue_item_factory):
    exec_mock = MagicMock()
    queue = get_dl_queue()
    mock_data: dict[int, AsyncIterator[QueueItem]] = {
        1: add_iter(
            [
                queue_item_factory(
                    payload__bug__id=1,
                    payload__event__time=datetime.now()
                    - timedelta(days=int(RETRY_TIMEOUT_DAYS), seconds=1),
                ),
                queue_item_factory(
                    payload__bug__id=1, payload__event__time=datetime.now()
                ),
            ]
        )
    }
    queue.retrieve = AsyncMock(return_value=mock_data)

    queue.done = AsyncMock()
    metrics = await retry_failed(item_executor=exec_mock)
    queue.retrieve.assert_called_once()
    assert (
        len(queue.done.call_args_list) == 2
    )  # both items should have been marked as done
    assert caplog.text.count("failed to reprocess event") == 0
    assert caplog.text.count("skipping events") == 0
    assert caplog.text.count("removing expired event") == 1
    exec_mock.assert_called_once()  # only one item should have been attempted to be processed
    assert metrics == {
        "bug_count": 1,
        "events_processed": 1,
        "events_skipped": 1,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_bug_failed(caplog, queue_item_factory):
    exec_mock = MagicMock()
    queue = get_dl_queue()
    mock_data: dict[int, AsyncIterator[QueueItem]] = {
        1: add_iter(
            [
                queue_item_factory(
                    payload__bug__id=1, payload__event__time=datetime.now()
                )
            ]
        ),
        2: iter_error(),
    }
    queue.retrieve = AsyncMock(return_value=mock_data)

    queue.done = AsyncMock()
    metrics = await retry_failed(item_executor=exec_mock)
    queue.retrieve.assert_called_once()
    queue.done.assert_called_once()  # one item should have been marked as done
    assert caplog.text.count("failed to reprocess event") == 0
    assert caplog.text.count("skipping events") == 0
    assert caplog.text.count("removing expired event") == 0
    assert caplog.text.count("failed to parse events for bug") == 1
    exec_mock.assert_called_once()  # only one item should have been attempted to be processed
    assert metrics == {
        "bug_count": 2,
        "events_processed": 1,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 1,
    }
