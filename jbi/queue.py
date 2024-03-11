import json
import logging
import os
import traceback
from abc import ABC
from datetime import datetime
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from jbi import bugzilla
from jbi.environment import get_settings

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

    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    retries: int = 0
    payload: bugzilla.WebhookRequest
    error: Optional[PythonException] = None


@lru_cache(maxsize=1)
def get_dl_queue():
    settings = get_settings()
    return DeadLetterQueue(settings.dl_queue_dsn)


class QueueBackend(ABC):
    """An interface for dead letter queues."""

    async def clear(self):
        pass

    async def put(self, item: QueueItem):
        pass

    async def get(self) -> list[QueueItem]:
        return []


class MemoryBackend(QueueBackend):
    def __init__(self):
        self.existing: list[QueueItem] = []

    async def clear(self):
        self.existing = []

    async def put(self, item: QueueItem):
        self.existing.append(item)

    async def get(self) -> list[QueueItem]:
        return self.existing


class FileBackend(QueueBackend):
    def __init__(self, location):
        self.location = location

    async def clear(self):
        os.path.remove(self.location)

    async def put(self, item: QueueItem):
        existing = await self.get()
        self._save(existing + [item])

    async def get(self) -> list[QueueItem]:
        with open(self.location) as f:
            data: list[QueueItem] = json.load(f)
        return data

    def _save(self, data: list[QueueItem]):
        with open(self.location, "w") as f:
            json.dump([i.model_dump() for i in data], f)
        logger.info("%s items in dead letter queue", len(data))


class InvalidQueueDSNError(Exception):
    pass


class DeadLetterQueue:
    backend: QueueBackend

    def __init__(self, dsn: str):
        parsed = urlparse(url=dsn)
        if parsed.scheme == "memory":
            self.backend = MemoryBackend()
        elif parsed.scheme == "file":
            self.backend = FileBackend(parsed.path)
        else:
            raise InvalidQueueDSNError(f"{parsed.scheme} is not supported")

    async def size(self):
        return len(await self.backend.get())

    async def postpone(self, payload: bugzilla.WebhookRequest):
        """
        Store the specified payload and exception information into the queue.
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
        existing = await self.backend.get()
        for item in existing:
            if item.payload.bug.id == payload.bug.id:
                return True
        return False

    async def clear(self):
        """
        Clear the whole queue.
        """
        await self.backend.clear()
