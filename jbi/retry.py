import asyncio
import logging
from datetime import UTC, datetime, timedelta
from os import getenv
from time import sleep

from dockerflow.logging import JsonLogFormatter

import jbi.runner as runner
from jbi.configuration import ACTIONS
from jbi.queue import get_dl_queue

CONSTANT_RETRY = getenv("DL_QUEUE_CONSTANT_RETRY", "false") == "true"
RETRY_TIMEOUT_DAYS = getenv("DL_QUEUE_RETRY_TIMEOUT_DAYS", 7)
CONSTANT_RETRY_SLEEP = getenv("DL_QUEUE_CONSTANT_RETRY_SLEEP", 5)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
lsh = logging.StreamHandler()
lsh.setFormatter(JsonLogFormatter(logger_name=__name__))
logger.addHandler(lsh)


async def retry_failed(item_executor=runner.execute_action, queue=get_dl_queue()):
    min_event_timestamp = datetime.now(UTC) - timedelta(days=int(RETRY_TIMEOUT_DAYS))

    # load all bugs from DLQ
    bugs = await queue.retrieve()

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
                # skip and delete item if we have exceeded RETRY_TIMEOUT_DAYS
                if item.timestamp < min_event_timestamp:
                    logger.warning("removing expired event %s", item.identifier)
                    await queue.done(item)
                    metrics["events_skipped"] += 1
                    continue

                try:
                    item_executor(item.payload, ACTIONS)
                    await queue.done(item)
                    metrics["events_processed"] += 1
                except Exception:
                    logger.exception("failed to reprocess event %s.", item.identifier)
                    metrics["events_failed"] += 1

                    # check for other events that will be skipped
                    pending_events = await queue.size(bug_id)
                    if pending_events > 1:  # if this isn't the only event for the bug
                        logger.info(
                            "skipping %d event(s) for bug %d, previous event %s failed",
                            pending_events - 1,
                            bug_id,
                            item.identifier,
                        )
                        metrics["events_skipped"] += pending_events - 1
                        break
        except Exception:
            logger.exception("failed to parse events for bug %d.", bug_id)
            metrics["bugs_failed"] += 1

    return metrics


async def main():
    while True:
        metrics = await retry_failed()
        logger.info("event queue processing complete", extra=metrics)
        if not CONSTANT_RETRY:
            return
        sleep(int(CONSTANT_RETRY_SLEEP))


if __name__ == "__main__":
    asyncio.run(main())
