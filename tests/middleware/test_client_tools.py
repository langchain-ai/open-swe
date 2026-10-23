"""A client tool's call is answered pending; the client answers it in a later run."""

from runtime_api.operations import RunOperation

from openswe.agent_api.graph import RunSession
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.openai_responses.client_tools import PENDING_ARTIFACT_KEY, ClientToolSpec
from openswe.sandboxes.tool_runtime import ToolSurface


async def test_a_client_call_is_answered_pending_for_the_client_to_answer() -> None:
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
