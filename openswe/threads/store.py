"""Threads as the backend reads and changes them: the PostgreSQL index and its runtimes.

The index answers every thread read; each runtime that keeps a thread holds a copy
of its metadata for its runs and traces, and runs its state and commands. Backend
code calls this directly; the thread router serves the Agent Server's thread API
from it for every other client. A thread an MDA channel owns is read there as
LangSmith Studio reads it, and changed only as the backend acting in its
conversation: its metadata is copied only here, and a command that adds to it
reaches MDA through MDA's trusted-backend ingress.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Final, NamedTuple

import httpx
from pydantic import JsonValue, TypeAdapter

from openswe.agent_api.client import (
    MDA_GRAPH_ID,
    MDA_RUNTIME,
    RUNTIME_METADATA_KEY,
    mda_graph_for,
)
from openswe.dispatch import channel_of
from openswe.threads import index
from openswe.threads.index import IndexedThread
from openswe.threads.runtimes import RUNTIMES, ChannelThreadError, ChannelWrite

logger = logging.getLogger(__name__)

_object = TypeAdapter(dict[str, JsonValue])
# Commands that add to a thread rather than read or stop it.
POSTING_COMMANDS: Final = frozenset({"run.start", "input.respond", "input.inject", "state.fork"})
# Protocol v2 commands that only watch a thread; ``run.start`` and ``input.respond`` act.
READ_COMMANDS: Final = frozenset(
    {
        "agent.getTree",
        "subscription.subscribe",
        "subscription.unsubscribe",
        "subscription.reconnect",
    }
)
# Commands the backend sends in a channel's conversation; it refuses the others there.
ACTING_COMMANDS: Final = frozenset({"run.start", "input.respond"})
READ_ONLY: Final = (
    "This thread predates Managed Deep Agents and is read-only. Start a new thread to continue."
)


def for_mda(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Metadata as MDA keeps it: there every Open SWE graph is ``open-swe``."""
    copied = dict(metadata)
    if "graph_id" in copied:
        copied["graph_id"] = MDA_GRAPH_ID
    return copied


def channel_write_refusal(thread: IndexedThread) -> str | None:
    """Why a write to ``thread`` is refused, or ``None`` when no MDA channel owns it."""
    channel = channel_of(thread.metadata)
    if channel is None:
        return None
    return (
        f"Thread {thread.thread_id} belongs to the {channel} channel, which starts "
        f"its runs: continue the conversation in {channel.capitalize()}."
    )


class CommandReply(NamedTuple):
    status: int
    content: bytes
    media_type: str | None


def _json_reply(status: int, payload: object) -> CommandReply:
    return CommandReply(status, json.dumps(payload).encode(), "application/json")


class _MetadataCopies:
    """Copies of thread metadata to the runtimes that keep each thread, made in the background.

    Reads come from the index and a runtime's copy only feeds its traces, so a write
    answers without waiting on the runtimes; one thread's copies still land in order.
    """

    def __init__(self) -> None:
        self._latest: dict[str, asyncio.Task[None]] = {}

    def start(self, thread: IndexedThread, patch: Mapping[str, JsonValue]) -> None:
        thread_id = thread.thread_id
        task = asyncio.create_task(self._copy(self._latest.get(thread_id), thread, patch))
        self._latest[thread_id] = task
        task.add_done_callback(lambda done: self._forget(thread_id, done))

    async def settled(self, thread_id: str) -> None:
        """Wait for the copies started so far of the thread's metadata."""
        if (latest := self._latest.get(thread_id)) is not None:
            await asyncio.wait([latest])

    def _forget(self, thread_id: str, task: asyncio.Task[None]) -> None:
        if self._latest.get(thread_id) is task:
            del self._latest[thread_id]

    @staticmethod
    async def _copy(
        after: asyncio.Task[None] | None, thread: IndexedThread, patch: Mapping[str, JsonValue]
    ) -> None:
        if after is not None:
            await asyncio.wait([after])
        # A channel's thread on MDA is the channel's; this deployment's copy feeds the traces here.
        copies = (
            [RUNTIMES.local] if channel_of(thread.metadata) else RUNTIMES.copies(thread.runtime)
        )
        for runtime in copies:
            try:
                await runtime.update(
                    thread.thread_id, for_mda(patch) if runtime is not RUNTIMES.local else patch
                )
            except Exception:
                logger.warning(
                    "Copying thread metadata to its runtime failed",
                    extra={"thread_id": thread.thread_id},
                    exc_info=True,
                )


class ThreadStore:
    """The thread index, with each change copied to the runtimes that keep the thread."""

    def __init__(self) -> None:
        self.copies = _MetadataCopies()

    async def get(self, thread_id: str) -> IndexedThread | None:
        """The indexed thread; one only the Agent Server knows is indexed on first use."""
        indexed = await index.get(thread_id)
        if indexed is not None:
            return indexed
        local = await RUNTIMES.local.indexed(thread_id)
        if local is None:
            return None
        logger.warning(
            "Indexed a thread only the Agent Server knew",
            extra={
                "thread_id": thread_id,
                "thread_runtime": local.runtime,
                "thread_status": local.status,
                "thread_created_at": local.created_at.isoformat(),
                "thread_updated_at": local.updated_at.isoformat(),
                "graph_id": local.metadata.get("graph_id"),
            },
        )
        return await index.create(
            thread_id,
            runtime=local.runtime,
            metadata=local.metadata,
            if_exists="do_nothing",
            status=local.status,
            created_at=local.created_at,
            updated_at=local.updated_at,
        )

    async def create(
        self, thread_id: str, *, title: str, metadata: Mapping[str, JsonValue]
    ) -> IndexedThread:
        """A titled thread of the backend's own on MDA, unless ``thread_id`` is one already,
        which keeps its metadata."""
        if (known := await self.get(thread_id)) is not None:
            return known
        thread = await index.create(
            thread_id,
            runtime="mda",
            metadata={**metadata, "title": title, RUNTIME_METADATA_KEY: MDA_RUNTIME},
            if_exists="do_nothing",
        )
        await RUNTIMES.local.create(thread_id, thread.metadata)
        await RUNTIMES.mda.create(thread_id, for_mda(thread.metadata))
        return thread

    async def update(
        self, thread_id: str, metadata: Mapping[str, JsonValue]
    ) -> IndexedThread | None:
        updated = await index.update(thread_id, metadata)
        if updated is not None and metadata:
            self.copies.start(updated, metadata)
        return updated

    async def copy(self, thread: IndexedThread, metadata: Mapping[str, JsonValue]) -> IndexedThread:
        """A new thread on MDA holding every checkpoint of ``thread``, indexed with its
        metadata and ``metadata`` over it."""
        if thread.runtime != "mda":
            raise ValueError(f"Thread {thread.thread_id} is not on MDA")
        made = _object.validate_python(
            await RUNTIMES.mda.client.http.post(f"/threads/{thread.thread_id}/copy", json={})
        )
        copy_id = made.get("thread_id")
        if not isinstance(copy_id, str):
            raise RuntimeError("MDA's thread copy returned no thread id")
        return await index.create(
            copy_id,
            runtime="mda",
            metadata={**thread.metadata, **metadata},
            if_exists="do_nothing",
        )

    async def discard_copy(self, thread: IndexedThread) -> None:
        """Delete a thread ``copy`` made, which only MDA and the index hold."""
        await RUNTIMES.mda.delete(thread.thread_id)
        await index.delete(thread.thread_id)

    async def delete(self, thread: IndexedThread) -> None:
        await self.copies.settled(thread.thread_id)
        for runtime in reversed(RUNTIMES.copies(thread.runtime)):
            await runtime.delete(thread.thread_id)
        await index.delete(thread.thread_id)

    async def state(self, thread_id: str) -> dict[str, JsonValue] | None:
        """The thread's state as its runtime keeps it, or ``None`` for an unknown thread."""
        thread = await self.get(thread_id)
        if thread is None:
            return None
        if thread.runtime == "mda" and channel_of(thread.metadata) is None:
            # The channel created its threads there; the backend's own may need it.
            await RUNTIMES.mda_runtime.ensure_remote_thread(thread_id)
        return await RUNTIMES.runs_on(thread.runtime).state(thread_id)

    async def command(
        self, thread_id: str, body: bytes, *, user_id: str | None = None
    ) -> CommandReply:
        """Answer one protocol v2 command as the thread's runtime does.

        ``user_id`` is the Open SWE user who sent it, which a run or an answer on a
        channel's thread acts as; without one it is the channel's own, for no person.
        """
        try:
            command = _object.validate_json(body)
        except ValueError:
            command = {}
        method = command.get("method")
        params = command.get("params")
        thread = await self.get(thread_id)
        if thread is not None and thread.runtime == "local":
            # Only threads made before every new one started on MDA are still ``local``.
            logger.warning(
                "Served a command on a thread that is not on Managed Deep Agents",
                extra={"thread_id": thread_id, "command_method": method},
            )
            if method in POSTING_COMMANDS:
                # Not 409, which callers read as "a run is already going" and retry or skip.
                return _json_reply(410, {"detail": READ_ONLY})
        refusal = channel_write_refusal(thread) if thread is not None else None
        write = ChannelWrite()
        if refusal is not None and method not in READ_COMMANDS | ACTING_COMMANDS:
            return _json_reply(409, {"detail": refusal})
        if refusal is not None and method in ACTING_COMMANDS:
            # A run or an answer on a channel's thread: as its sender, in its conversation.
            try:
                write = await RUNTIMES.mda_runtime.channel_write(thread_id, user_id=user_id)
            except ChannelThreadError, httpx.HTTPError:
                logger.warning(
                    "Can't act on an MDA channel's thread",
                    extra={"thread_id": thread_id},
                    exc_info=True,
                )
                if method == "run.start":
                    return _json_reply(
                        200,
                        {
                            "type": "error",
                            "id": command.get("id", 0),
                            "error": "invalid_argument",
                            "message": refusal,
                        },
                    )
                return _json_reply(409, {"detail": refusal})
        if thread is not None and method == "run.start" and isinstance(params, dict):
            reply = await RUNTIMES.mda_runtime.start_command_run(
                thread_id,
                command,
                params,
                thread_metadata=thread.metadata,
                graph_id=mda_graph_for(thread.metadata),
                write=write,
            )
            if reply.get("type") == "success":
                try:
                    await index.set_status(thread_id, "busy")
                except Exception:
                    # The run exists; its completion webhook and the reconcile job settle the status.
                    logger.warning(
                        "Marking a thread busy after starting its run failed",
                        extra={"thread_id": thread_id},
                        exc_info=True,
                    )
            return _json_reply(200, reply)
        if thread is not None and thread.runtime == "mda":
            mda = RUNTIMES.mda_runtime
            if refusal is not None:
                # A channel's thread: read as Studio reads it, written as its conversation.
                http = mda.http if write.headers else mda.read_http
            else:
                await mda.ensure_remote_thread(thread_id)
                http = mda.http
        else:
            http = RUNTIMES.local.client.http.client
        response = await http.post(
            f"/threads/{thread_id}/commands",
            content=body,
            headers={"Content-Type": "application/json", **write.headers},
        )
        return CommandReply(
            response.status_code, response.content, response.headers.get("content-type")
        )


THREADS: Final = ThreadStore()
