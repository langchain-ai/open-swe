"""A run stops at a client tool call and resumes on the client's result."""

from typing import cast
from unittest.mock import MagicMock

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from runtime_api.operations import RunOperation

from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.openai_responses.client_tools import PENDING_ARTIFACT_KEY, ClientToolSpec
from openswe.remote_runtime.graph import RunSession
from openswe.sandboxes.tool_runtime import ToolSurface


async def test_a_client_call_is_answered_pending_and_the_run_stops_until_the_client_does() -> None:
    spec = ClientToolSpec(
        kind="function",
        name="exec_command",
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
    )
    client = ClientToolsMiddleware([spec])
    surface = ToolSurface(middleware=[client])
    await surface.prepare()
    session = RunSession(surface, {"configurable": {}}, None, started_here=True)

    pending = await session.operation(
        RunOperation(name="exec_command", arguments={"cmd": "ls"}, request_id="call-1")
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
