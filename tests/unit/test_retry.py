from datetime import datetime, timedelta
from unittest.mock import MagicMock

import os
import pytest

from jbi.retry import RETRY_TIMEOUT_DAYS, retry_failed
from jbi.runner import execute_action
from jbi.queue import QueueItem


def iter_error():
    mock = MagicMock()
    mock.__aiter__.return_value = None
    mock.__aiter__.side_effect = Exception("Throwing an exception")
    return mock


async def aiter_sync(iterable):
    for i in iterable:
        yield i


@pytest.fixture
def mock_executor():
    return MagicMock(spec=execute_action)


@pytest.mark.asyncio
async def test_retry_empty_list(caplog, mock_queue):
    mock_queue.retrieve.return_value = {}

    metrics = await retry_failed(queue=mock_queue)
    mock_queue.retrieve.assert_called_once()
    assert len(caplog.messages) == 0
    assert metrics == {
        "bug_count": 0,
        "events_processed": 0,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_success(caplog, mock_queue, mock_executor, queue_item_factory):
    mock_queue.retrieve.return_value = {
        1: aiter_sync([queue_item_factory(payload__bug__id=1)])
    }

    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    assert len(caplog.messages) == 0  # no logs should have been generated
    mock_queue.retrieve.assert_called_once()
    mock_queue.done.assert_called_once()  # item should be marked as complete
    mock_executor.assert_called_once()  # item should have been processed
    assert metrics == {
        "bug_count": 1,
        "events_processed": 1,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_fail_and_skip(
    caplog, mock_queue, mock_executor, queue_item_factory
):
    mock_queue.retrieve.return_value = {
        1: aiter_sync(
            [
                queue_item_factory(payload__bug__id=1),
                queue_item_factory(payload__bug__id=1),
            ]
        )
    }

    mock_executor.side_effect = Exception("Throwing an exception")
    mock_queue.size.return_value = 3

    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    mock_queue.retrieve.assert_called_once()
    mock_queue.done.assert_not_called()  # no items should have been marked as done
    assert caplog.text.count("failed to reprocess event") == 1
    assert caplog.text.count("skipping 2 event(s)") == 1
    assert caplog.text.count("removing expired event") == 0
    mock_executor.assert_called_once()  # only one item should have been attempted to be processed
    assert metrics == {
        "bug_count": 1,
        "events_processed": 0,
        "events_skipped": 2,
        "events_failed": 1,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_remove_expired(
    caplog, mock_queue, mock_executor, queue_item_factory
):
    mock_queue.retrieve.return_value = {
        1: aiter_sync(
            [
                queue_item_factory(
                    payload__bug__id=1,
                    payload__event__time=datetime.now()
                    - timedelta(days=int(RETRY_TIMEOUT_DAYS), seconds=1),
                ),
                queue_item_factory(payload__bug__id=1),
            ]
        )
    }

    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    mock_queue.retrieve.assert_called_once()
    assert (
        len(mock_queue.done.call_args_list) == 2
    ), "both items should have been marked as done"
    assert caplog.text.count("failed to reprocess event") == 0
    assert caplog.text.count("skipping events") == 0
    assert caplog.text.count("removing expired event") == 1
    mock_executor.assert_called_once()  # only one item should have been attempted to be processed
    assert metrics == {
        "bug_count": 1,
        "events_processed": 1,
        "events_skipped": 1,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_bug_failed(caplog, mock_queue, mock_executor, queue_item_factory):
    mock_queue.retrieve.return_value = {
        1: aiter_sync([queue_item_factory(payload__bug__id=1)]),
        2: iter_error(),
    }

    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    mock_queue.retrieve.assert_called_once()
    mock_queue.done.assert_called_once()  # one item should have been marked as done
    assert caplog.text.count("failed to reprocess event") == 0
    assert caplog.text.count("skipping events") == 0
    assert caplog.text.count("removing expired event") == 0
    assert caplog.text.count("failed to parse events for bug") == 1
    mock_executor.assert_called_once()  # only one item should have been attempted to be processed
    assert metrics == {
        "bug_count": 2,
        "events_processed": 1,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 1,
    }


@pytest.mark.asyncio
async def test_with_file(caplog, mock_queue, mock_executor):
    mock_queue.retrieve.return_value = {
        1: aiter_sync([QueueItem.parse_file("tests/fixtures/test_retry_file.json")])
    }
    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    assert metrics == {
        "bug_count": 1,
        "events_processed": 1,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }
