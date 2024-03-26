import asyncio
import logging
from datetime import datetime, timedelta
from os import getenv
from time import sleep
from typing import AsyncIterator

import jbi.runner as runner
from jbi.configuration import ACTIONS
from jbi.queue import QueueItem, get_dl_queue

CONSTANT_RETRY = getenv("CONSTANT_RETRY", "false") == "true"
RETRY_TIMEOUT_DAYS = getenv("RETRY_TIMEOUT_DAYS", 7)


async def retry_failed():
    queue = get_dl_queue()
    logger = logging.getLogger(__name__)
    min_event_timestamp = datetime.now() - timedelta(days=int(RETRY_TIMEOUT_DAYS))

    # load all bugs from DLQ
    bugs: dict[int, AsyncIterator[QueueItem]] = await queue.retrieve()

    for bugid, items in bugs.items():
        prev_failed = False
        async for item in items:
            bug = item.payload.bug

            # skip if any previous retries for this bug have already failed
            if prev_failed:
                logger.info(
                    "skipping event %s - previous events have failed for this bug",
                    item.identifier,
                )
                continue

            # skip and delete item if we have exceeded max_timeout
            if item.timestamp < min_event_timestamp:
                logger.warn("removing expired event %s", item.identifier)
                await queue.done(item)
                continue

            try:
                runner.execute_action(item.payload, ACTIONS)
                await queue.done(item)
            except Exception as ex:
                # write well formed log that could be alerted on
                logger.error(
                    "failed to reprocess event %s. error: %s", item.identifier, ex
                )
                prev_failed = True


async def main():
    while True:
        await retry_failed()
        if not CONSTANT_RETRY:
            return
        sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
