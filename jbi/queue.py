import bisect
import itertools
import logging
import shutil
import tempfile
import traceback
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel, Field

from jbi import bugzilla
from jbi.environment import DLQUrl, get_settings

logger = logging.getLogger(__name__)


class PythonException(BaseModel, frozen=True):
    type: str
    description: str
    details: str

    @classmethod
    def from_exc(cls, exc: Exception):
        return PythonException(
            type=exc.__class__.__name__,
            description=str(exc),
            details="".join(traceback.format_exception(exc)),
        )


class QueueItem(BaseModel, frozen=True):
    """Dead Letter Queue entry."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    payload: bugzilla.WebhookRequest
    error: Optional[PythonException] = None

    @property
    def identifier(self):
        return f"{self.timestamp.isoformat()}-{self.payload.bug.id}-{self.payload.event.action}-{"error" if self.error else "postponed"}"


@lru_cache(maxsize=1)
def get_dl_queue():
    settings = get_settings()
    return DeadLetterQueue(settings.dl_queue_dsn)


class QueueBackend(ABC):
    """An interface for dead letter queues."""

    @abstractmethod
    def ping(self):
        """Report if the queue backend is available and ready to be written to"""
        pass

    @abstractmethod
    async def clear(self):
        pass

    @abstractmethod
    async def put(self, item: QueueItem):
        """Insert item into queued items for a bug, maintaining sorted order by
        payload event time ascending
        """
        pass

    @abstractmethod
    async def remove(self, bug_id: int, identifier: str):
        pass

    @abstractmethod
    async def get(self, bug_id: int) -> list[QueueItem]:
        """Retrieve all of the queue items for a specific bug, sorted in
        ascending order by the timestamp of the payload event.
        """
        pass

    @abstractmethod
    async def get_all(self) -> dict[int, list[QueueItem]]:
        """Retrive all items in the queue, grouped by bug

        Returns:
            dict[int, list[QueueItem]]: Returns a dict of
            {bug_id: list of events}. Each list of events sorted in ascending
            order by the timestamp of the payload event.
        """
        pass

    @property
    @abstractmethod
    def size(self) -> int:
        pass


class MemoryBackend(QueueBackend):
    def __init__(self):
        self.existing: dict[int, list[QueueItem]] = defaultdict(list)

    def ping(self):
        return True

    async def clear(self):
        self.existing.clear()

    async def put(self, item: QueueItem):
        bisect.insort_right(
            self.existing[item.payload.bug.id],
            item,
            key=lambda i: i.payload.event.time or 0,
        )

    async def get(self, bug_id: int) -> list[QueueItem]:
        # though we're using a defaultdict, check if the bug is in the queue
        # this way so that we don't create a key for a bug with no items
        if bug_id not in self.existing:
            return []
        return self.existing[bug_id]

    async def get_all(self) -> dict[int, list[QueueItem]]:
        return self.existing

    async def remove(self, bug_id: int, identifier: str):
        """Remove an item from the target bug's queue. If the item is the last
        one in the queue, delete the bug's key from the dict.
        """
        filtered_items = [
            i for i in self.existing[bug_id] if i.identifier != identifier
        ]
        if not len(filtered_items):
            del self.existing[bug_id]
        else:
            self.existing[bug_id] = filtered_items

    @property
    def size(self) -> int:
        return sum(len(v) for v in self.existing.values())


class FileBackend(QueueBackend):
    def __init__(self, location):
        self.location = Path(location)
        self.location.mkdir(parents=True, exist_ok=True)

    def ping(self):
        try:
            with tempfile.TemporaryDirectory(dir=self.location) as temp_dir:
                with tempfile.TemporaryFile(dir=temp_dir) as f:
                    f.write(b"")
            return True
        except Exception:
            logger.exception("Could not write to file backed queue")
            return False

    async def clear(self):
        shutil.rmtree(self.location)

    async def put(self, item: QueueItem):
        folder = self.location / f"{item.payload.bug.id}"
        folder.mkdir(exist_ok=True)
        path = folder / (item.identifier + ".json")
        path.write_text(item.model_dump_json())
        logger.info("%d items in dead letter queue", self.size)

    async def remove(self, bug_id: int, identifier: str):
        """Remove an item from the target bug's queue. If the item is the last
        one in the queue, delete the directory.
        """
        bug_dir = self.location / f"{bug_id}"
        item_path = bug_dir / (identifier + ".json")
        item_path.unlink(missing_ok=True)
        if not any(bug_dir.iterdir()):
            bug_dir.rmdir()

    async def get(self, bug_id: int) -> list[QueueItem]:
        folder = self.location / f"{bug_id}"
        if not folder.is_dir():
            return []
        items = (QueueItem.parse_file(path) for path in folder.iterdir())
        return list(sorted(items, key=lambda i: i.payload.event.time or 0))

    async def get_all(self) -> dict[int, list[QueueItem]]:
        all_items: dict[int, list[QueueItem]] = defaultdict(list)
        for filepath in self.location.rglob("*.json"):
            item = QueueItem.parse_file(filepath)
            bisect.insort_right(
                all_items[item.payload.bug.id],
                item,
                key=lambda i: i.payload.event.time or 0,
            )
        return all_items

    @property
    def size(self) -> int:
        return sum(1 for _ in self.location.rglob("*.json"))


class InvalidQueueDSNError(Exception):
    pass


class DeadLetterQueue:
    backend: QueueBackend

    def __init__(self, dsn: DLQUrl | str | ParseResult):
        if isinstance(dsn, str):
            dsn = urlparse(url=dsn)

        if dsn.scheme == "memory":
            self.backend = MemoryBackend()
        elif dsn.scheme == "file":
            self.backend = FileBackend(dsn.path)
        else:
            raise InvalidQueueDSNError(f"{dsn.scheme} is not supported")

    async def postpone(self, payload: bugzilla.WebhookRequest):
        """
        Postpone the specified request for later.
        """
        item = QueueItem(payload=payload)
        await self.backend.put(item)

    async def track_failed(self, payload: bugzilla.WebhookRequest, exc: Exception):
        """
        Store the specified payload and exception information into the queue.
        """
        item = QueueItem(
            payload=payload,
            error=PythonException.from_exc(exc),
        )
        await self.backend.put(item)

    async def is_blocked(self, payload: bugzilla.WebhookRequest) -> bool:
        """
        Return `True` if the specified `payload` is blocked and should be
        queued instead of being processed.
        """
        existing = await self.backend.get(payload.bug.id)
        return len(existing) > 0

    async def retrieve(self) -> list[QueueItem]:
        """
        Returns the whole list of items in the queue, grouped by bug, and
        sorted from oldest to newest.
        """
        existing = await self.backend.get_all()
        # Sort by event datetime ascending.
        by_oldest_bug_events = sorted(
            existing.values(), key=lambda items: items[0].payload.event.time or 0
        )
        return list(itertools.chain(*by_oldest_bug_events))

    async def done(self, item: QueueItem):
        """
        Mark item as done, remove from queue.
        """
        return await self.backend.remove(item.payload.bug.id, item.identifier)
