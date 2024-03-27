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


async def retry_failed(item_executor=runner.execute_action):
    queue = get_dl_queue()
    logger = logging.getLogger(__name__)
    min_event_timestamp = datetime.now() - timedelta(days=int(RETRY_TIMEOUT_DAYS))

    # load all bugs from DLQ
    bugs: dict[int, AsyncIterator[QueueItem]] = await queue.retrieve()

    # metrics to track
    metrics = {
        "bug_count": len(bugs),
        "events_processed": 0,
        "events_skipped": 0,
        "events_failed": 0,
        "bugs_failed": 0,
    }

    for bug_id, items in bugs.items():
        try:
            async for item in items:
                # skip and delete item if we have exceeded max_timeout
                if item.timestamp < min_event_timestamp:
                    logger.warning("removing expired event %s", item.identifier)
                    await queue.done(item)
                    metrics["events_skipped"] += 1
                    continue

                try:
                    item_executor(item.payload, ACTIONS)
                    await queue.done(item)
                    metrics["events_processed"] += 1
                except Exception as ex:
                    # write well formed log that could be alerted on
                    logger.error(
                        "failed to reprocess event %s. error: %s", item.identifier, ex
                    )
                    metrics["events_failed"] += 1

                    # check for other events that will be skipped
                    skipped_events = await queue.list(bug_id)
                    if (
                        len(skipped_events) > 1
                    ):  # if this isn't the only event for the bug
                        logger.info(
                            "skipping events %s - previous event %s failed for this bug",
                            ",".join(skipped_events),
                            item.identifier,
                        )
                        metrics["events_skipped"] += len(skipped_events) - 1
                        break
        except Exception as ex:
            logger.error("failed to parse events for bug %d. error: %s", bug_id, ex)
            metrics["bugs_failed"] += 1

    return metrics


async def main():
    logger = logging.getLogger(__name__)
    while True:
        metrics = await retry_failed()
        logger.info("event queue processing complete", extra=metrics)
        if not CONSTANT_RETRY:
            return
        sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
