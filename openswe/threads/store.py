"""Threads as the backend reads and changes them: the PostgreSQL index and its runtimes.

The index answers every thread read; the runtime a thread runs on holds a copy of
its metadata for its runs and traces, and runs its state and commands. Backend
code calls this directly; the thread router serves the Agent Server's thread API
from it for every other client. A thread an MDA channel owns is read there as
LangSmith Studio reads it, and changed only as the backend acting in its
conversation: its metadata stays in the index, and a command that adds to it
reaches MDA through MDA's trusted-backend ingress.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import nullcontext
from typing import Final, NamedTuple

import httpx
import httpx2
from pydantic import JsonValue, TypeAdapter

from openswe.agent_api.client import (
    MDA_GRAPH_ID,
    MDA_RUNTIME,
    RUNTIME_METADATA_KEY,
    mda_graph_for,
)
from openswe.dispatch import channel_of
from openswe.sandboxes.this_mac import MacSandbox
from openswe.tasks.store import sidebar_memberships
from openswe.threads import index
from openswe.threads.index import IndexedThread, ThreadStatus
from openswe.threads.runtimes import RUNTIMES, ChannelThreadError, ChannelWrite, now_ms
from openswe.threads.start_request import RunRefused, StartRequest
from openswe.transcript.attachments import InlineImage, ThreadAttachments
from openswe.transcript.message_times import MessageTimes
from openswe.transcript.mirror import mirror_thread_metadata
from openswe.utils.startup_trace import apm_span
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock
from openswe.utils.timing import phase

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
READ_ONLY: Final = (
    "This thread predates Managed Deep Agents and is read-only. Start a new thread to continue."
)
MAC_THREAD: Final = (
    "This Mac threads are no longer supported. Start a new cloud thread to continue."
)
# What a provider has the model's next call send back verbatim, by the content block that
# carries it: OpenAI's encrypted reasoning, Anthropic's thinking signature and redacted
# thinking. Each sits on the block as the provider returned it, or under the ``extras`` of
# LangChain's standard ``reasoning`` block.
_REPLAY_ONLY: Final[Mapping[str, tuple[str, ...]]] = {
    "reasoning": ("encrypted_content", "signature"),
    "thinking": ("signature",),
    "redacted_thinking": ("data",),
}


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

    @classmethod
    def json(cls, status: int, payload: object) -> CommandReply:
        return cls(status, json.dumps(payload).encode(), "application/json")


class _MetadataCopies:
    """Copies of thread metadata to the runtime each thread runs on, made in the background.

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
        # A channel's thread on MDA is the channel's; the backend writes nothing there.
        if channel_of(thread.metadata) is not None:
            return
        try:
            if thread.runtime == "mda":
                await RUNTIMES.mda.update(thread.thread_id, for_mda(patch))
            else:
                await RUNTIMES.local.update(thread.thread_id, patch)
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

    async def search(self, query: index.ThreadSearch) -> list[IndexedThread]:
        return await query.run()

    async def get_many(self, thread_ids: Sequence[str]) -> dict[str, IndexedThread]:
        """Indexed threads by id, importing any unindexed legacy threads on first read."""
        unique = list(dict.fromkeys(thread_ids))
        if not unique:
            return {}
        found = {
            thread.thread_id: thread
            for thread in await self.search(index.ThreadSearch(ids=unique, limit=len(unique)))
        }
        missing = [thread_id for thread_id in unique if thread_id not in found]
        if missing:
            loaded = await asyncio.gather(
                *(self.get(thread_id) for thread_id in missing), return_exceptions=True
            )
            for thread_id, item in zip(missing, loaded, strict=True):
                if isinstance(item, BaseException):
                    logger.warning(
                        "Could not read thread from store",
                        exc_info=item,
                        extra={"agent_thread_id": thread_id},
                    )
                elif item is not None:
                    found[thread_id] = item
        return found

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
        from openswe.tasks.schemas import ThreadMetadata

        if (known := await self.get(thread_id)) is not None:
            return known
        metadata = await ThreadMetadata.for_new_thread(metadata)
        thread = await index.create(
            thread_id,
            runtime="mda",
            metadata={**metadata, "title": title, RUNTIME_METADATA_KEY: MDA_RUNTIME},
            if_exists="do_nothing",
        )
        await RUNTIMES.mda.create(thread_id, for_mda(thread.metadata))
        return thread

    async def update(
        self, thread_id: str, metadata: Mapping[str, JsonValue]
    ) -> IndexedThread | None:
        updated = await index.update(thread_id, metadata)
        if updated is not None and metadata:
            self.copies.start(updated, metadata)
        return updated

    async def run_payload(
        self,
        thread_id: str,
        payload: dict[str, JsonValue],
        *,
        thread_metadata: Mapping[str, JsonValue],
        graph_id: str,
    ) -> dict[str, JsonValue]:
        """``payload`` rewritten for the thread's MDA graph, once its metadata copies landed.

        Working out the run can write the thread's metadata, such as its settings, and
        MDA's run create waits only about a second for the thread's row a copy holds.
        """
        rewritten = await RUNTIMES.mda_runtime.run_payload(
            thread_id, payload, thread_metadata=thread_metadata, graph_id=graph_id
        )
        with apm_span("start_run.copies_settled", {"thread_id": thread_id}):
            await self.copies.settled(thread_id)
        return rewritten

    async def archive_task_workers(
        self,
        coordinator_thread_id: str,
        metadata: Mapping[str, JsonValue],
        *,
        check: Callable[[str], Awaitable[object]] | None = None,
    ) -> None:
        """Apply a coordinator's archive ``metadata`` to its task workers that ``check`` allows."""
        try:
            workers = await sidebar_memberships([coordinator_thread_id], workers_of=True)
        except Exception:
            logger.exception(
                "Could not list task workers during archive",
                extra={"thread_id": coordinator_thread_id},
            )
            return
        for worker_id in workers:
            try:
                async with agent_thread_pr_state_lock(worker_id):
                    if check is not None:
                        await check(worker_id)
                    await self.update(worker_id, metadata)
            except Exception:
                logger.exception(
                    "Could not archive task worker",
                    extra={"thread_id": worker_id, "coordinator_thread_id": coordinator_thread_id},
                )

    async def retitle(self, thread_id: str, title: str) -> None:
        """Give the thread a title of the system's choosing, unless a person renamed it."""
        metadata: dict[str, JsonValue] = {"title": title, "title_seed": None}
        updated = await index.update(thread_id, metadata, unless_title_locked=True)
        if updated is None:
            return
        self.copies.start(updated, metadata)
        await mirror_thread_metadata(thread_id, metadata)

    async def update_if_current(
        self,
        thread: IndexedThread,
        *,
        metadata: Mapping[str, JsonValue],
        status: ThreadStatus,
    ) -> IndexedThread | None:
        """Merge a runtime observation without replacing a concurrent writer's newer state."""
        updated = await thread.update_if_current(metadata, status)
        if updated is not None and metadata:
            self.copies.start(updated, metadata)
        return updated

    async def delete(self, thread: IndexedThread) -> None:
        from openswe.tasks.messages import TaskMessage

        await self.copies.settled(thread.thread_id)
        await RUNTIMES.runs_on(thread.runtime).delete(thread.thread_id)
        if thread.runtime == "mda":
            # MDA threads made before the Agent Server was sealed kept a copy here too.
            await RUNTIMES.local.delete(thread.thread_id)
        await index.delete(thread.thread_id)
        await TaskMessage.forget(thread.thread_id)

    async def state(
        self, thread_id: str, *, select: list[str] | None = None
    ) -> dict[str, JsonValue] | None:
        """The thread's state as its runtime keeps it, or ``None`` for an unknown thread."""
        thread = await self.get(thread_id)
        if thread is None:
            return None
        return await self.state_of(thread, select=select)

    async def state_of(
        self,
        thread: IndexedThread,
        *,
        select: list[str] | None = None,
        timings: dict[str, float] | None = None,
    ) -> dict[str, JsonValue]:
        """The state of a thread already read from the index, as its runtime keeps it.

        ``select`` names the fields to return, as the Agent Server takes them; the runtime
        validates them. ``timings`` gets the phases of the runtime's read, which add up over
        both reads of a thread it recreates.
        """
        runtime = RUNTIMES.runs_on(thread.runtime)
        try:
            return await runtime.state(thread.thread_id, select=select, timings=timings)
        except (httpx.HTTPStatusError, httpx2.HTTPStatusError) as error:
            # Indexed with no copy on MDA: creating it there failed, or MDA swept it. A
            # channel makes its own threads there, so the backend makes only its own again.
            if (
                error.response.status_code != httpx.codes.NOT_FOUND
                or thread.runtime != "mda"
                or channel_of(thread.metadata) is not None
            ):
                raise
            logger.warning(
                "Making an indexed thread MDA has no copy of", extra={"thread_id": thread.thread_id}
            )
        await runtime.create(thread.thread_id, for_mda(thread.metadata))
        return await runtime.state(thread.thread_id, select=select, timings=timings)

    async def client_state_of(
        self,
        thread: IndexedThread,
        *,
        select: list[str] | None = None,
        timings: dict[str, float] | None = None,
    ) -> dict[str, JsonValue]:
        """:meth:`state_of` as a client reads it. What a provider has the model's next call
        send back verbatim (:data:`_REPLAY_ONLY`) is left out, since no client reads it, and
        an image the transcript keeps a copy of is a link to that copy, which a client loads
        when it shows the image. On a long thread each is megabytes gzip can't shrink; the
        thread's own state keeps both. Each message the transcript recorded carries when it
        was sent (:class:`MessageTimes`), which the state itself doesn't."""
        state = await self.state_of(thread, select=select, timings=timings)
        values = state.get("values")
        messages = values.get("messages") if isinstance(values, dict) else None
        for message in messages if isinstance(messages, list) else []:
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or not isinstance(kind := block.get("type"), str):
                    continue
                extras = block.get("extras")
                for key in _REPLAY_ONLY.get(kind, ()):
                    block.pop(key, None)
                    if isinstance(extras, dict):
                        extras.pop(key, None)
        if isinstance(messages, list) and (images := InlineImage.all_in(messages)):
            with phase(timings if timings is not None else {}, "media_links"):
                stored = await ThreadAttachments.of(thread.thread_id)
                stored.link(images)
        dicts = [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []
        ids = [message_id for m in dicts if isinstance(message_id := m.get("id"), str)]
        if ids:
            with phase(timings if timings is not None else {}, "message_times"):
                times = await MessageTimes.of(thread.thread_id, ids)
                times.stamp(dicts)
        return state

    async def with_latest_run(self, thread: IndexedThread) -> IndexedThread:
        """The thread with its latest run's id and status in its metadata, as its runtime reports them.

        A changed run is recorded in the index too. When the runtime can't be read, the
        thread comes back as the index has it.
        """
        try:
            run = await RUNTIMES.runs_on(thread.runtime).latest_run(thread.thread_id)
        except Exception:
            logger.warning(
                "Could not read a thread's latest run",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )
            return thread
        if run is None:
            return thread
        latest: dict[str, JsonValue] = {
            "latest_run_id": run.run_id,
            "latest_run_status": run.status,
        }
        if all(thread.metadata.get(key) == value for key, value in latest.items()):
            return thread
        try:
            await self.update(thread.thread_id, latest)
        except Exception:
            # The run read still decides how the thread reads.
            logger.warning(
                "Could not record a thread's latest run",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )
        return thread.model_copy(update={"metadata": {**thread.metadata, **latest}})

    async def command(self, thread_id: str, body: bytes) -> CommandReply:
        """Answer one of protocol v2's other commands as the thread's runtime does: neither
        a run's start nor an answer, which :meth:`start_run` starts.

        On a thread an MDA channel owns, it reads as Studio reads, and a write is refused.
        """
        try:
            command = _object.validate_json(body)
        except ValueError:
            command = {}
        method = command.get("method")
        thread = await self.get(thread_id)
        if thread is not None and thread.runtime == "local":
            # Only threads made before every new one started on MDA are still ``local``.
            logger.warning(
                "Served a command on a thread that is not on Managed Deep Agents",
                extra={"thread_id": thread_id, "command_method": method},
            )
            if method in POSTING_COMMANDS:
                # Not 409, which callers read as "a run is already going" and retry or skip.
                return CommandReply.json(410, {"detail": READ_ONLY})
        refusal = channel_write_refusal(thread) if thread is not None else None
        if refusal is not None and method not in READ_COMMANDS:
            return CommandReply.json(409, {"detail": refusal})
        if thread is not None and thread.runtime == "mda":
            mda = RUNTIMES.mda_runtime
            if refusal is None:
                await mda.ensure_remote_thread(thread_id)
            # A channel's thread: read as Studio reads it.
            http = mda.read_http if refusal is not None else mda.http
        else:
            http = RUNTIMES.local.client.http.client
        async with http if isinstance(http, httpx2.AsyncClient) else nullcontext(http):
            response = await http.post(
                f"/threads/{thread_id}/commands",
                content=body,
                headers={"Content-Type": "application/json"},
            )
        return CommandReply(
            response.status_code, response.content, response.headers.get("content-type")
        )

    async def move_off_mac(self, thread: IndexedThread) -> IndexedThread:
        """Move a This Mac thread to a new cloud sandbox, where its run starts instead.

        Raises :class:`RunRefused` for a task worker, whose sandbox was its coordinator's.
        """
        if thread.metadata.get("task_id") is not None:
            raise RunRefused(410, MAC_THREAD)
        moved = await self.update(thread.thread_id, MacSandbox.move_update())
        logger.info("Moved a This Mac thread to the cloud", extra={"thread_id": thread.thread_id})
        return moved or thread

    async def start_run(
        self,
        thread_id: str,
        start: StartRequest,
        *,
        user_id: str | None = None,
        trace_headers: Mapping[str, str] | None = None,
    ) -> str:
        """Start ``start`` on the thread's runtime, and return its run's id.

        ``user_id`` is the Open SWE user who sent it, which a run on a channel's thread
        acts as; without one it is the channel's own, for no person. ``trace_headers`` name
        a LangSmith trace the run nests in. A run that can't start raises
        :class:`RunRefused`.
        """
        thread = await self.get(thread_id)
        if thread is None:
            raise RunRefused(404, "thread not found")
        if thread.runtime == "local":
            # Only threads made before every new one started on MDA are still ``local``.
            logger.warning(
                "Served a command on a thread that is not on Managed Deep Agents",
                extra={"thread_id": thread_id, "command_method": "run.start"},
            )
            # Not 409, which callers read as "a run is already going" and retry or skip.
            raise RunRefused(410, READ_ONLY)
        if thread.on_a_mac:
            thread = await self.move_off_mac(thread)
        write = ChannelWrite()
        if (refusal := channel_write_refusal(thread)) is not None:
            # A run on a channel's thread: as its sender, in its conversation.
            try:
                write = await RUNTIMES.mda_runtime.channel_write(thread_id, user_id=user_id)
            except ChannelThreadError, httpx2.HTTPError:
                logger.warning(
                    "Can't act on an MDA channel's thread",
                    extra={"thread_id": thread_id},
                    exc_info=True,
                )
                # Protocol v2 refused a start with its error, and an answer with a status.
                code = None if start.command is not None else "invalid_argument"
                raise RunRefused(409, refusal, code=code) from None
        mda = RUNTIMES.mda_runtime
        starting_ms = now_ms()
        run = await self.run_payload(
            thread_id,
            mda.run_body(thread_id, start, write=write),
            thread_metadata=thread.metadata,
            graph_id=mda_graph_for(thread.metadata),
        )
        run_id = await mda.create_run(
            thread_id,
            run,
            starting_ms=starting_ms,
            write=write,
            trace_headers=trace_headers,
        )
        try:
            await index.set_status(thread_id, "busy")
        except Exception:
            # The run exists; its completion webhook and the reconcile job settle the status.
            logger.warning(
                "Marking a thread busy after starting its run failed",
                extra={"thread_id": thread_id},
                exc_info=True,
            )
        return run_id


THREADS: Final = ThreadStore()
