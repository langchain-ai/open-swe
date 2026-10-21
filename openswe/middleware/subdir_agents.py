"""Auto-load applicable ``AGENTS.md`` files after file reads, from the thread's sandbox here."""

from deepagents.backends.protocol import BackendProtocol
from langgraph.prebuilt.tool_node import ToolCallRequest
from openswe_shared.subdir_agents import BaseSubdirAgentsReadMiddleware, thread_id

from openswe.sandboxes.state import SANDBOX_BACKENDS


class SubdirAgentsReadMiddleware(BaseSubdirAgentsReadMiddleware):
    def backend(self, request: ToolCallRequest) -> BackendProtocol | None:
        thread = thread_id(request)
        return SANDBOX_BACKENDS.get(thread) if thread else None
