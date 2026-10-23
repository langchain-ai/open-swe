"""The tools a plugin offers the agent it chats beside, which the plugin's own frame runs.

A call ends the run like any client tool's, and is recorded for the dock beside
the plugin, which runs it in the plugin's frame and resumes the thread with its
result. The system prompt says which plugin is open and what its tools are for.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.plugins.records import PluginChatCalls
from openswe.prompts import prompt
from openswe.run_config import PluginChatConfig
from openswe.utils.json_types import JsonObject


class PluginChatMiddleware(ClientToolsMiddleware):
    def __init__(self, chat: PluginChatConfig, *, thread_id: str) -> None:
        super().__init__(chat.tools)
        self.calls = PluginChatCalls(UUID(chat.chat_id), thread_id)
        self.instructions = prompt(
            "plugins/chat",
            plugin=chat.plugin,
            prefix=chat.prefix,
            instructions=chat.instructions,
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await super().awrap_tool_call(request, handler)
        call = request.tool_call
        if call["name"] in self._names and call["id"]:
            arguments: JsonObject = dict(call["args"])
            await self.calls.record(call["id"], call["name"], arguments)
        return result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": self.instructions})
        return await handler(request.override(system_message=SystemMessage(content_blocks=blocks)))
