from contextlib import asynccontextmanager
import os

from jbi import bugzilla


def get_dl_queue():
    return DeadLetterQueue()


class DeadLetterQueue:
    def __init__(self):
        self.filename = "f.json"

    async def _store(self, payload: bugzilla.WebhookRequest, error: Exception):
        with open(self.filename, "w") as f:
            f.write("touch")

    def _should_skip(self, payload: bugzilla.WebhookRequest):
        return os.path.exists(self.filename)

    async def purge(self):
        os.path.remove(self.filename)

    @asynccontextmanager
    async def receive(self, payload: bugzilla.WebhookRequest):
        if self._should_skip(payload):
            yield None
            return

        try:
            yield payload
        except Exception as error:
            await self._store(payload, error)
            raise
