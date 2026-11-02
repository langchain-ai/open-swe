"""A run stops at a client tool call and resumes on the client's result."""

from typing import cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from runtime_api.operations import OperationResult, RunOperation

from openswe.agent_api.run_calls import TokenRun
from openswe.agent_api.run_parts import RunParts
from openswe.agent_api.tokens import RemoteRun
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.openai_responses.client_tools import PENDING_ARTIFACT_KEY, ClientToolSpec


class _Parts(RunParts):
    assistant_id = "agent"


async def test_a_client_call_is_answered_pending_and_the_run_stops_until_the_client_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")
    spec = ClientToolSpec(
        kind="function",
        name="exec_command",
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
    )
    client = ClientToolsMiddleware([spec])
    run = RemoteRun(
        thread_id="thread-1",
        assistant_id="agent",
        configurable={"client_tools": [spec.model_dump(mode="json")]},
    )
    operation = RunOperation(
        name="exec_command",
        arguments={"cmd": "ls"},
        request_id="call-1",
        grant=run.grant("exec_command"),
    )

    pending = OperationResult.model_validate(
        await TokenRun.answer(_Parts(), run, "operation", operation.model_dump(mode="json"))
    )

    # The client's real result arrives with the next run under this id, replacing it.
    assert pending.message_id == ClientToolSpec.result_message_id("call-1")
    assert pending.artifact == {PENDING_ARTIFACT_KEY: True}
    waiting = cast(
        AgentState,
        {
            "messages": [
                HumanMessage("list files"),
                AIMessage("", tool_calls=[{"name": "exec_command", "args": {}, "id": "call-1"}]),
                ToolMessage(
                    content=str(pending.content),
                    tool_call_id="call-1",
                    artifact=pending.artifact,
                    id=pending.message_id,
                ),
            ]
        },
    )
    assert await client.abefore_model(waiting, MagicMock()) == {"jump_to": "end"}
    answered = cast(
        AgentState,
        {
            "messages": [
                *waiting["messages"][:2],
                ToolMessage(content="a.py\nb.py", tool_call_id="call-1", id=pending.message_id),
            ]
        },
    )
    assert await client.abefore_model(answered, MagicMock()) is None
