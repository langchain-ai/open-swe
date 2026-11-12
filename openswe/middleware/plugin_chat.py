"""The tools a plugin offers the agent it chats beside, which the plugin's own frame runs.

A call waits like any client tool's, and is recorded for the dock beside the plugin,
which runs it in the plugin's frame and resumes the thread with its result. The run's
system prompt says only what Open SWE knows: which plugin is open, which tools are its,
and that what it sends is its data. What the plugin says about its tools is its author's
words, so it reaches the agent in the conversation, with the person's messages from its
dock (``openswe.threads.runs``).
"""

from uuid import UUID

from langchain_core.messages import ToolMessage

from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.plugins.records import PluginChatCalls
from openswe.prompts import prompt
from openswe.run_config import PluginChatConfig, RunConfig
from openswe.utils.json_types import JsonObject


class PluginChatTools(ClientToolsMiddleware):
    def __init__(self, chat: PluginChatConfig, *, thread_id: str) -> None:
        super().__init__(chat.tools)
        self.calls = PluginChatCalls(UUID(chat.chat_id), thread_id)
        self.instructions = prompt("plugins/chat", plugin=chat.plugin, prefix=chat.prefix)

    @staticmethod
    def of_run(cfg: RunConfig, thread_id: str) -> ClientToolsMiddleware | None:
        """The tools the run's client answers: its own, else those of the plugin it chats beside."""
        if cfg.client_tools:
            return ClientToolsMiddleware(cfg.client_tools)
        if cfg.plugin_chat is not None:
            return PluginChatTools(cfg.plugin_chat, thread_id=thread_id)
        return None

    async def pending_result(
        self, name: str, call_id: str, arguments: JsonObject
    ) -> ToolMessage | None:
        pending = await super().pending_result(name, call_id, arguments)
        if pending is not None:
            await self.calls.record(call_id, name, arguments)
        return pending
