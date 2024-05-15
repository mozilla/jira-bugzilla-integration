from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from jbi.errors import IgnoreInvalidRequestError
from jbi.retry import RETRY_TIMEOUT_DAYS, retry_failed
from jbi.runner import execute_action


def mock_aiter_error():
    _mock = mock.MagicMock()
    _mock.__aiter__.return_value = None
    _mock.__aiter__.side_effect = Exception("Throwing an exception")
    return _mock


async def aiter_sync(iterable):
    for i in iterable:
        yield i


@pytest.fixture
def mock_executor(mocker):
    return mocker.MagicMock(spec=execute_action)


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
    assert len(caplog.messages) == 1  # only one log been generated
    assert caplog.text.count("retry event") == 1
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
                    payload__event__time=datetime.now(UTC)
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
async def test_retry_remove_invalid(
    caplog, mock_queue, mock_executor, queue_item_factory
):
    mock_queue.retrieve.return_value = {
        1: aiter_sync(queue_item_factory.create_batch(2))
    }
    mock_executor.side_effect = [
        IgnoreInvalidRequestError("How did this get in here"),
        mock.DEFAULT,
    ]
    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    assert (
        len(mock_queue.done.call_args_list) == 2
    ), "both items should have been marked as done"
    assert caplog.text.count("removing invalid event") == 1
    assert metrics == {
        "bug_count": 1,
        "events_processed": 2,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }


@pytest.mark.asyncio
async def test_retry_bug_failed(caplog, mock_queue, mock_executor, queue_item_factory):
    mock_queue.retrieve.return_value = {
        1: aiter_sync([queue_item_factory(payload__bug__id=1)]),
        2: mock_aiter_error(),
    }

    metrics = await retry_failed(item_executor=mock_executor, queue=mock_queue)
    mock_queue.retrieve.assert_called_once()
    mock_queue.done.assert_called_once()  # one item should have been marked as done
    assert caplog.text.count("failed to reprocess event") == 0
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
async def test_original_rid_is_put_in_retry_logs(
    caplog, authenticated_client, bugzilla_webhook_request, dl_queue, mocked_bugzilla
):
    mocked_bugzilla.get_bug.side_effect = ValueError("Boom!")

    # Post an event that will fail.
    assert (await dl_queue.size()) == 0
    authenticated_client.post(
        "/bugzilla_webhook",
        data=bugzilla_webhook_request.model_dump_json(),
    )
    logged = [r for r in caplog.records if r.name == "jbi.runner"]
    original_rid = logged[0].rid
    assert original_rid, "rid was set in logs when webhook is received"
    assert (await dl_queue.size()) == 1, "an event was put in queue"

    # Reset log capture and retry the queue.
    caplog.clear()
    assert len(caplog.records) == 0
    metrics = await retry_failed(queue=dl_queue)

    # Inspect retry logs.
    assert metrics["events_failed"] == 1, "event failed again"
    assert (await dl_queue.size()) == 1, "an event still in queue"
    logged = [r for r in caplog.records if r.name == "jbi.runner"]
    assert logged[0].rid == original_rid, "logs of retry have original request id"
