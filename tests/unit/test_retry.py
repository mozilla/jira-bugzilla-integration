import logging
from datetime import datetime, timedelta
from os import getenv
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

import jbi.runner
from jbi.bugzilla.models import Bug, WebhookEvent, WebhookRequest
from jbi.queue import QueueItem, get_dl_queue
from jbi.retry import retry_failed

RETRY_TIMEOUT_DAYS = getenv("RETRY_TIMEOUT_DAYS", 7)


@pytest.fixture()
def logger():
    logger = logging.getLogger(__name__)
    logger.info = MagicMock()
    logger.warn = MagicMock()
    logger.error = MagicMock()
    logging.getLogger = lambda x=None: logger
    return logger


@pytest.fixture()
def execute_action():
    _execute_action = MagicMock()
    jbi.runner.execute_action = _execute_action
    return _execute_action


@pytest.mark.asyncio
async def test_retry_empty_list(logger):
    retrieve = AsyncMock(return_value={})
    get_dl_queue().retrieve = retrieve

    await retry_failed()
    retrieve.assert_called_once()
    logger.info.assert_not_called()
    logger.warn.assert_not_called()
    logger.error.assert_not_called()


def add_iter(obj):
    mock = MagicMock()
    mock.__aiter__.return_value = obj
    return mock


@pytest.mark.asyncio
async def test_retry_success(logger, execute_action):
    queue = get_dl_queue()
    queue.retrieve = AsyncMock(
        return_value={
            1: add_iter([
                QueueItem(
                    timestamp=datetime.now(),
                    payload=WebhookRequest(
                        webhook_id=1,
                        webhook_name="test",
                        bug=Bug(id=1),
                        event=WebhookEvent(action="test"),
                    ),
                )
            ])
        }
    )
    queue.done = AsyncMock()

    await retry_failed()
    queue.retrieve.assert_called_once()
    queue.done.assert_called_once() # item should be marked as complete
    logger.info.assert_not_called() # no items should have been skipped or failed
    logger.warn.assert_not_called()
    logger.error.assert_not_called()
    execute_action.assert_called_once() # item should have been processed


@pytest.mark.asyncio
async def test_retry_fail_and_skip(logger, execute_action):
    queue = get_dl_queue()
    queue.retrieve = AsyncMock(
        return_value={
            1: add_iter([
                QueueItem(
                    timestamp=datetime.now(),
                    payload=WebhookRequest(
                        webhook_id=1,
                        webhook_name="test1",
                        bug=Bug(id=1),
                        event=WebhookEvent(action="test1"),
                    ),
                ),
                QueueItem(
                    timestamp=datetime.now(),
                    payload=WebhookRequest(
                        webhook_id=2,
                        webhook_name="test2",
                        bug=Bug(id=1),
                        event=WebhookEvent(action="test2"),
                    ),
                ),
            ])
        }
    )

    execute_action.side_effect = Exception("Throwing an exception")

    queue.done = AsyncMock()
    await retry_failed()
    queue.retrieve.assert_called_once()
    queue.done.assert_not_called()  # no items should have been marked as done
    logger.info.assert_called_once()  # one item should be logged as skipped
    logger.warn.assert_not_called()  # no items should have been marked as expired
    logger.error.assert_called_once()  # one item should have caused an exception
    execute_action.assert_called_once()  # only one item should have been attempted to be processed


@pytest.mark.asyncio
async def test_retry_remove_expired(logger, execute_action):
    queue = get_dl_queue()
    mock_data: dict[int, AsyncIterator[QueueItem]] = {
            1: add_iter([
                QueueItem(
                timestamp=datetime.now()
                - timedelta(days=int(RETRY_TIMEOUT_DAYS), seconds=1),
                payload=WebhookRequest(
                    webhook_id=1,
                    webhook_name="test1",
                    bug=Bug(id=1),
                    event=WebhookEvent(action="test1"),
                ),
            ),
            QueueItem(
                timestamp=datetime.now(),
                payload=WebhookRequest(
                    webhook_id=2,
                    webhook_name="test2",
                    bug=Bug(id=1),
                    event=WebhookEvent(action="test2"),
                ),
            ),
        ])
    }
    queue.retrieve = AsyncMock(return_value=mock_data)

    queue.done = AsyncMock()
    await retry_failed()
    queue.retrieve.assert_called_once()
    assert (
        len(queue.done.call_args_list) == 2
    )  # both items should have been marked as done
    logger.info.assert_not_called()  # no items should be logged as skipped
    logger.warn.assert_called_once()  # one item should have been marked as expired
    logger.error.assert_not_called()  # no item should have caused an exception
    execute_action.assert_called_once()  # only one item should have been attempted to be processed
