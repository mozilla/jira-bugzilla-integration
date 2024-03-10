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
    def from_exc_info(cls, exc_info: tuple):
        exc_type, exc_value, _exc_context = exc_info
        return PythonException(
            type=exc_type.__name__,
            description=str(exc_value),
            details="".join(traceback.format_exception(*exc_info)),
        )


class QueueItem(BaseModel, frozen=True):
    """Dead Letter Queue entry."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    retries: int = 0
    payload: bugzilla.WebhookRequest
    error: Optional[PythonException]


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

    async def receive(
        self, payload: bugzilla.WebhookRequest
    ) -> Optional[bugzilla.WebhookRequest]:
        if await self.is_blocked(payload):
            # If it's blocked, store it and wait for it to be processed later.
            await self.backend.put(QueueItem(payload=payload, error=None))
            logger.info(
                "%r event on Bug %s was put in queue for later processing.",
                payload.event.action,
                payload.bug.id,
                extra={"payload": payload.model_dump()},
            )
            return None

        # TODO: potentially merge it with other events.

        return payload

    async def store(self, payload: bugzilla.WebhookRequest, exc_info: tuple):
        """
        Store the specified payload and exception information into the queue.
        """
        item = QueueItem(
            payload=payload,
            error=PythonException.from_exc_info(exc_info),
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
