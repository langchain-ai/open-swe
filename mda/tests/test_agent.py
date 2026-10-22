import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import agent as mda_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage, message_to_dict
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.runtime import Runtime
from rt.backend import Catalog, OpenSweBackend, SubagentSpec, ToolReply, ToolSpec


class SubagentToolsTest(unittest.IsolatedAsyncioTestCase):
    @patch.dict("os.environ", {"OPEN_SWE_BACKEND_URL": "https://backend.invalid"})
    async def test_discovery_and_code_calls_respect_permissions(self) -> None:
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}
        catalog = Catalog(
            tools=[
                ToolSpec(name=name, description="Lookup records", parameters=parameters)
                for name in (
                    "lookup_records",
                    "get_thread",
                    "list_threads",
                    "hidden",
                    "search_tools",
                )
            ],
            subagents=[
                SubagentSpec(
                    name="general-purpose",
                    description="Lookup records",
                    system_prompt="",
                    tools=["lookup_records", "hidden", "search_tools"],
                )
            ],
            excluded=["hidden"],
            code_only=["lookup_records", "get_thread", "list_threads", "hidden"],
            also_from_code=["search_tools"],
        )
        backend = OpenSweBackend("token")

        async def call(body: dict[str, object]) -> ToolReply:
            arguments = body["arguments"]
            self.assertIsInstance(arguments, dict)
            if body["name"] == "search_tools":
                self.assertEqual(set(arguments["names"]), {"lookup_records", "search_tools"})
                result = json.dumps(["lookupRecords"])
            else:
                self.assertEqual(body["name"], "lookup_records")
                result = f"lookup_records: {arguments['query']}"
            return ToolReply(
                message=message_to_dict(
                    ToolMessage(content=result, tool_call_id=str(body["tool_call_id"]))
                )
            )

        with (
            patch.object(backend, "catalog", AsyncMock(return_value=catalog)),
            patch.object(backend, "tool", call),
            patch.object(mda_agent, "OpenSweBackend", return_value=backend),
            patch.object(mda_agent, "define_deep_agent", side_effect=lambda **kwargs: kwargs),
            patch("rt.tools.run_id", return_value="run-1"),
        ):
            definition = await mda_agent.agent(
                SimpleNamespace(execution_runtime=SimpleNamespace(context={"__run_token": "token"}))
            )
            subagent = definition["subagents"][0]
            interpreter = next(
                middleware
                for middleware in subagent["middleware"]
                if isinstance(middleware, CodeInterpreterMiddleware)
            )
            state = {"messages": []}
            state.update(await interpreter.abefore_agent(state, Runtime()) or {})

            async def respond(request: ModelRequest) -> ModelResponse:
                return ModelResponse(result=[])

            await interpreter.awrap_model_call(
                ModelRequest(
                    model=FakeListChatModel(responses=[]),
                    messages=[],
                    state=state,
                    tools=subagent["tools"],
                ),
                respond,
            )
            runtime = ToolRuntime(
                state=state,
                context=None,
                config={},
                stream_writer=lambda event: None,
                tool_call_id="eval-1",
                store=None,
            )
            [evaluate] = interpreter.tools
            result = await evaluate.coroutine(
                runtime=runtime,
                code='console.log(await tools.searchTools({query: "lookup", names: ["get_thread", "list_threads", "hidden"]})); console.log(await tools.lookupRecords({query: "prior outcome"})); console.log(typeof tools.getThread, typeof tools.listThreads, typeof tools.hidden);',
            )
            self.assertEqual(result.status, "success")
            self.assertIn("lookupRecords", result.content)
            self.assertIn("lookup_records: prior outcome", result.content)
            self.assertIn("undefined undefined undefined", result.content)
            # Release the interpreter's slot, or its finalizer blocks interpreter exit.
            await interpreter.aafter_agent(state, Runtime())
