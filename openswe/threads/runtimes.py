"""Where a thread's runs live: this deployment's Agent Server, or Managed Deep Agents.

Both keep a copy of the thread's metadata for their runs and traces. The local
Agent Server keeps one for every thread, so switching ``THREAD_STORE`` back to
``agent_server`` finds each thread as the index had it.
"""

import logging
from collections.abc import Mapping
from typing import Final

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from pydantic import JsonValue, TypeAdapter

from openswe.remote_runtime.client import mda_runtime_url
from openswe.remote_runtime.proxy import LOCAL_HEADER, MdaRuntime
from openswe.threads.index import INDEXED_FIELDS, IndexedThread, Runtime, ThreadStatus

logger = logging.getLogger(__name__)

_object = TypeAdapter(dict[str, JsonValue])


class _SdkThreads:
    """A runtime reached through the LangGraph SDK."""

    def __init__(self, client: LangGraphClient) -> None:
        self.client = client

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        try:
            return _object.validate_python(dict(await self.client.threads.get(thread_id)))
        except NotFoundError:
            return None

    async def indexed(self, thread_id: str) -> IndexedThread | None:
        """The thread as the index keeps it, read without its values."""
        found = await self.client.threads.search(ids=[thread_id], limit=1, select=INDEXED_FIELDS)
        return next(
            (thread for raw in found if (thread := IndexedThread.from_agent_server(raw))), None
        )

    async def create(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        await self.client.threads.create(
            thread_id=thread_id, if_exists="do_nothing", metadata=dict(metadata)
        )

    async def update(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        await self.client.threads.update(thread_id, metadata=dict(metadata))

    async def delete(self, thread_id: str) -> None:
        try:
            await self.client.threads.delete(thread_id)
        except NotFoundError:
            return

    async def status(self, thread_id: str) -> ThreadStatus | None:
        """The thread's status here; one this runtime does not have has nothing running."""
        thread = await self.indexed(thread_id)
        return "idle" if thread is None else thread.status

    async def has_history(self, thread_id: str) -> bool:
        """Whether runs already left checkpoints here, which keeps the thread on this runtime."""
        thread = await self.get(thread_id)
        return thread is not None and bool(thread.get("values") or thread.get("status") != "idle")


class Runtimes:
    """This deployment's Agent Server, and the MDA deployment when one is configured."""

    def __init__(self) -> None:
        self._mda: MdaRuntime | None = None

    @property
    def local(self) -> _SdkThreads:
        # Past the thread router, straight to the Agent Server's own routes.
        return _SdkThreads(get_client(headers={LOCAL_HEADER: "1"}))

    @property
    def mda_runtime(self) -> MdaRuntime | None:
        url = mda_runtime_url()
        if url is None:
            return None
        if self._mda is None or self._mda.url != url:
            self._mda = MdaRuntime(url)
        return self._mda

    @property
    def mda(self) -> _SdkThreads:
        runtime = self.mda_runtime
        if runtime is None:
            raise LookupError("MDA_RUNTIME_URL is not configured")
        return _SdkThreads(runtime.sdk())

    def runs_on(self, runtime: Runtime) -> _SdkThreads:
        return self.mda if runtime == "mda" else self.local

    def copies(self, runtime: Runtime) -> list[_SdkThreads]:
        """Every runtime that keeps a copy of a thread on ``runtime``."""
        return [self.local, self.mda] if runtime == "mda" else [self.local]


RUNTIMES: Final = Runtimes()
