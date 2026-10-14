"""A run hosted on another runtime, streaming its replies into the backend's transcript.

The runtime half (``openswe_transcript.remote``) posts to the backend half
(``openswe.remote_runtime.transcript``) through the real route and run token,
into a real migrated schema: the turn binding and the refusals are SQL.
"""

import itertools
from datetime import UTC, datetime
from uuid import UUID, uuid7

import httpx2
import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from openswe_transcript import remote, stream
from openswe_transcript.remote import HttpFragmentSink, RemoteTranscriptMiddleware
from openswe_transcript.wire import PATH, Fragment, FragmentBatch, streamed_message_id
from starlette.applications import Starlette
from starlette.routing import Route

from openswe.remote_runtime.tokens import RemoteRun, sign_runtime_token
from openswe.remote_runtime.transcript import TRANSCRIPT
from openswe.transcript.engine import Command, append
from openswe.transcript.events import (
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    TurnCompleted,
    TurnRequested,
    TurnStarted,
)
from openswe.transcript.snapshot import load_snapshot

THREAD_ID = "remote-thread"
RUN_ID = "remote-run"


@pytest.fixture(autouse=True)
def token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "signing-key-0123456789abcdef0123456789")


def _token() -> str:
    return sign_runtime_token(RemoteRun(thread_id=THREAD_ID, assistant_id="agent", configurable={}))


def _backend() -> httpx2.ASGITransport:
    return httpx2.ASGITransport(app=Starlette(routes=[Route(PATH, TRANSCRIPT, methods=["POST"])]))


async def _start_turn() -> UUID:
    """What the run's hooks record before its first model call."""
    turn_id = uuid7()
    await append(
        THREAD_ID,
        [
            Command(
                command_id=f"thread:{THREAD_ID}:created",
                event=ThreadCreated(title="t", source="dashboard", owner_login="someone"),
                actor_kind="system",
            ),
            Command(
                command_id=f"turn:{turn_id}:requested",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="human-1",
                    text="do the thing",
                    sender=MessageSender(login="someone", kind="dashboard"),
                ),
                actor_kind="user",
                turn_id=turn_id,
            ),
            Command(
                command_id=f"turn:{turn_id}:started:{RUN_ID}",
                event=TurnStarted(turn_id=turn_id, run_id=RUN_ID),
                actor_kind="agent",
                run_id=RUN_ID,
                turn_id=turn_id,
            ),
        ],
    )
    return turn_id


async def test_a_remote_reply_streams_into_its_runs_turn_before_the_call_returns(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn_id = await _start_turn()
    manager = AsyncCallbackManager(handlers=[])
    config = {"configurable": {"thread_id": THREAD_ID, "run_id": RUN_ID}, "callbacks": manager}
    monkeypatch.setattr(stream, "get_config", lambda: config)
    monkeypatch.setattr(remote, "get_config", lambda: config)
    monkeypatch.setattr(stream, "PARAGRAPH_FLUSH_SECONDS", 0)
    middleware = RemoteTranscriptMiddleware(
        HttpFragmentSink("http://backend", _token(), transport=_backend())
    )
    reply = AIMessage(content="First paragraph.\n\nSecond.", id="resp_final")

    async def model(request: ModelRequest) -> ModelResponse:
        capture = manager.handlers[-1]
        for token in ("First paragraph.\n\n", "Second."):
            await capture.on_llm_new_token(
                token,
                chunk=ChatGenerationChunk(message=AIMessageChunk(content=token)),
                run_id=uuid7(),
            )
        return ModelResponse(result=[reply])

    request = ModelRequest(
        model=GenericFakeChatModel(messages=itertools.cycle([reply])),
        messages=[],
        state={"messages": []},
        runtime=None,
    )
    await middleware.awrap_model_call(request, model)

    # The reply names the message its fragments used, for the backend to complete.
    streamed_id = streamed_message_id(reply)
    assert streamed_id is not None and streamed_id != "resp_final"
    snapshot = await load_snapshot(THREAD_ID)
    [message] = [m for m in snapshot.messages if m.role == "ai"]
    assert (message.message_id, message.turn_id) == (streamed_id, turn_id)
    assert message.text == "First paragraph.\n\nSecond."


async def test_the_backend_appends_only_to_the_runs_open_turn_and_its_ai_messages(
    registry_db: None,
) -> None:
    turn_id = await _start_turn()

    def batch(message_id: str, *, run_id: str = RUN_ID) -> str:
        fragment = Fragment(fragment_id=uuid7(), message_id=message_id, text="x")
        return FragmentBatch(run_id=run_id, fragments=[fragment]).model_dump_json()

    async with httpx2.AsyncClient(transport=_backend(), base_url="http://backend") as client:

        async def post(body: str, token: str | None = _token()) -> httpx2.Response:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            return await client.post(PATH, headers=headers, content=body)

        delivered = batch("ai-1")
        assert (await post(delivered)).json() == {"appended": 1}
        assert (await post(delivered)).json() == {"appended": 0}
        assert (await post(batch("ai-1"), token=None)).status_code == 401
        # What a person said, and a run with no turn of its own.
        assert (await post(batch("human-1"))).status_code == 409
        assert (await post(batch("ai-1", run_id="another-run"))).status_code == 409

        await append(
            THREAD_ID,
            [
                Command(
                    command_id="message:ai-1:completed",
                    event=MessageCompleted(
                        turn_id=turn_id,
                        message_id="ai-1",
                        role="ai",
                        text="canonical",
                        created_at=datetime.now(UTC),
                    ),
                    actor_kind="agent",
                    run_id=RUN_ID,
                    turn_id=turn_id,
                )
            ],
        )
        # A late fragment of a completed message would duplicate its tail.
        assert (await post(batch("ai-1"))).json() == {"appended": 0}

        await append(
            THREAD_ID,
            [
                Command(
                    command_id=f"turn:{turn_id}:completed",
                    event=TurnCompleted(turn_id=turn_id, run_id=RUN_ID),
                    actor_kind="agent",
                    run_id=RUN_ID,
                    turn_id=turn_id,
                )
            ],
        )
        assert (await post(batch("ai-2"))).status_code == 409

    [message] = [m for m in (await load_snapshot(THREAD_ID)).messages if m.role == "ai"]
    assert message.text == "canonical"
