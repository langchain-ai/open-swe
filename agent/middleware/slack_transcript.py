"""Deliver what the agent says to a Slack code channel, from inside the run.

A code channel shows a run the way the dashboard does, except the dashboard
reads the thread and Slack has to be told. Telling it belongs *in* the run: the
platform re-queues a run whose pod dies and ``durability="sync"`` resumes it
from its last checkpoint, so middleware is resumed with it and keeps writing
into the message it was already writing. An observer outside the run dies with
its own process and leaves a live run talking to nobody.

Only committed words go out. Each turn delivers the assistant text already in
state — checkpointed, with stable ids — rather than what the model just said,
so a step that dies before its checkpoint has said nothing that needs taking
back. Which message the transcript is being written into, and which message ids
have gone out, live in the store for the same reason.

The run is identified by ``prepare_run_id``: it is minted per dispatch, stored
in the run's own config, and therefore survives a resume — unlike a
callback-scoped id, and unlike anything held in memory.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_store
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from agent.middleware.message_content import content_to_text
from agent.surfaces.projector import (
    SlackTranscript,
    shows_its_own_effect,
    transcript_namespace,
)
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


def _failed(result: Any) -> bool:
    return isinstance(result, ToolMessage) and getattr(result, "status", "") == "error"


class SlackTranscriptMiddleware(AgentMiddleware[Any, Any, Any]):
    """Stream a run's words and tool activity into its Slack channel."""

    def __init__(
        self,
        *,
        thread_id: str,
        run_key: str,
        channel_id: str,
        reply_thread_ts: str,
        session_thread_ts: str,
        triggering_user_id: str = "",
        triggering_event_ts: str = "",
        team_id: str = "",
    ) -> None:
        super().__init__()
        self.thread_id = thread_id
        self.run_key = run_key
        self.channel_id = channel_id
        self.reply_thread_ts = reply_thread_ts
        self.session_thread_ts = session_thread_ts
        self.triggering_user_id = triggering_user_id
        self.triggering_event_ts = triggering_event_ts
        self.team_id = team_id
        self._transcript: SlackTranscript | None = None

    # ------------------------------------------------------------------ store

    def _store(self) -> BaseStore | None:
        try:
            return get_store()
        except Exception:  # noqa: BLE001
            logger.debug("No store for the Slack transcript of %s", self.run_key, exc_info=True)
            return None

    async def _record(self) -> dict[str, Any]:
        store = self._store()
        if store is None:
            return {}
        try:
            item = await store.aget(transcript_namespace(self.thread_id), self.run_key)
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the transcript record for %s", self.run_key, exc_info=True)
            return {}
        value = getattr(item, "value", None)
        return dict(value) if isinstance(value, dict) else {}

    async def _save(self, record: dict[str, Any]) -> None:
        store = self._store()
        if store is None:
            return
        try:
            await store.aput(transcript_namespace(self.thread_id), self.run_key, record)
        except Exception:  # noqa: BLE001
            logger.warning("Could not persist the transcript of %s", self.run_key, exc_info=True)

    # ------------------------------------------------------------- transcript

    async def _ensure(self) -> tuple[SlackTranscript, dict[str, Any]] | None:
        """This turn's transcript, resumed when one is already being written."""
        record = await self._record()
        if self._transcript is not None:
            return self._transcript, record
        transcript = SlackTranscript(
            client=langgraph_client(),
            thread_id=self.thread_id,
            run_id=self.run_key,
            channel_id=self.channel_id,
            thread_ts=self.reply_thread_ts or self.session_thread_ts,
            recipient_user_id=self.triggering_user_id,
            recipient_team_id=self.team_id,
            mapping_thread_ts=self.session_thread_ts,
            original_message_ts=self.triggering_event_ts,
        )
        message_ts = record.get("message_ts")
        if isinstance(message_ts, str) and message_ts:
            transcript.message_ts = message_ts
            streamed = record.get("streamed_chars")
            transcript.streamed_chars = streamed if isinstance(streamed, int) else 0
            pending = record.get("pending")
            transcript.pending = (
                [chunk for chunk in pending if isinstance(chunk, dict)]
                if isinstance(pending, list)
                else []
            )
        elif not await transcript.start():
            return None
        else:
            record["message_ts"] = transcript.message_ts
            record["channel_id"] = transcript.channel_id
            await self._save(record)
        self._transcript = transcript
        return transcript, record

    async def _flush(self, transcript: SlackTranscript, record: dict[str, Any]) -> None:
        """Push what is queued, and record what Slack has not taken yet.

        `flush` holds chunks back on a rate limit rather than failing, so the
        queue is part of the turn's durable state: without it a resumed run
        would treat rate-limited words as delivered and never say them.
        """
        await transcript.flush(force=True)
        record["message_ts"] = transcript.message_ts
        record["streamed_chars"] = transcript.streamed_chars
        record["pending"] = transcript.pending
        record["channel_id"] = transcript.channel_id
        await self._save(record)

    def _unsent(self, state: AgentState, record: dict[str, Any]) -> list[tuple[str, str]]:
        """Assistant text in committed state that has not gone out yet."""
        skip = {
            value
            for key in ("sent", "baseline")
            for value in record.get(key, [])
            if isinstance(value, str)
        }
        unsent: list[tuple[str, str]] = []
        for message in state.get("messages", []):
            message_id = getattr(message, "id", None)
            if not isinstance(message_id, str) or message_id in skip:
                continue
            if not isinstance(message, AIMessage):
                continue
            text = content_to_text(getattr(message, "content", "") or "").strip()
            if text:
                unsent.append((message_id, text))
        return unsent

    async def _say_committed(self, state: AgentState) -> None:
        prepared = await self._ensure()
        if prepared is None:
            return
        transcript, record = prepared
        unsent = self._unsent(state, record)
        if not unsent:
            return
        for _, text in unsent:
            transcript.say(text)
        record["sent"] = [*record.get("sent", []), *(message_id for message_id, _ in unsent)]
        await self._flush(transcript, record)

    # ----------------------------------------------------------------- hooks

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        """Open a turn: note what the conversation already held, so only this turn goes out."""
        record = await self._record()
        if record and not record.get("done"):
            return
        await self._save(
            {
                "baseline": [
                    message_id
                    for message in state.get("messages", [])
                    if isinstance(message_id := getattr(message, "id", None), str)
                ]
            }
        )

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> None:
        await self._say_committed(state)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        raw_state = getattr(request, "state", None)
        state = cast(AgentState, raw_state if isinstance(raw_state, dict) else {"messages": []})
        call = getattr(request, "tool_call", None)
        call = call if isinstance(call, dict) else {}
        call_id = str(call.get("id") or "")
        name = str(call.get("name") or "")
        if not call_id or not name:
            return await handler(request)
        # The message asking for this call is checkpointed by now, so saying it
        # here puts the agent's words ahead of the card they explain.
        await self._say_committed(state)
        prepared = None if shows_its_own_effect(name) else await self._ensure()
        if prepared is not None:
            prepared[0].tool_started(call_id, name, call.get("args"))
            await self._flush(*prepared)
        try:
            result = await handler(request)
        except Exception:
            if prepared is not None:
                prepared[0].tool_finished(call_id, failed=True)
                await self._flush(*prepared)
            raise
        if prepared is not None:
            # A tool that raised is turned into an error result before it reaches
            # here, so the result is what says whether the call failed.
            prepared[0].tool_finished(call_id, failed=_failed(result))
            await self._flush(*prepared)
        return result

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:
        """Close the turn, so a resumed or repeated run opens its own message."""
        await self._say_committed(state)
        if self._transcript is None:
            return
        await self._transcript.stop("success")
        record = await self._record()
        record["done"] = True
        await self._save(record)
