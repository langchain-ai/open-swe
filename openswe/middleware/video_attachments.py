"""Stage video inputs on the run's prepared sandbox before model invocation."""

import base64
from uuid import uuid7

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from openswe.prompts import prompt
from openswe.transcript.attachments import MAX_ATTACHMENT_BYTES

VIDEO_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/mpeg": ".mpeg",
}


class VideoAttachmentsMiddleware(AgentMiddleware):
    """Replace validated video blocks with paths after sandbox preparation."""

    def __init__(self, backend: BackendProtocol) -> None:
        self.backend = backend

    async def abefore_agent(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, list[HumanMessage]] | None:
        return await self.abefore_model(state, runtime)

    async def abefore_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, list[HumanMessage]] | None:
        files: list[tuple[str, bytes]] = []
        messages: list[HumanMessage] = []
        for message in state["messages"]:
            if not isinstance(message, HumanMessage) or not isinstance(message.content, list):
                continue
            content: list[str | dict[str, object]] = []
            paths: list[str] = []
            for block in message.content:
                if not isinstance(block, dict) or block.get("type") != "video":
                    content.append(block)
                    continue
                mime_type = block.get("mime_type")
                encoded = block.get("base64")
                if not isinstance(mime_type, str) or mime_type not in VIDEO_EXTENSIONS:
                    raise ValueError("Unsupported video attachment type")
                if (
                    not isinstance(encoded, str)
                    or len(encoded) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4
                ):
                    raise ValueError("Video attachment exceeds the 10MB limit")
                data = base64.b64decode(encoded, validate=True)
                if len(data) > MAX_ATTACHMENT_BYTES:
                    raise ValueError("Video attachment exceeds the 10MB limit")
                path = f"/workspace/.open-swe/uploads/{uuid7()}{VIDEO_EXTENSIONS[mime_type]}"
                files.append((path, data))
                paths.append(path)
            if paths:
                content.append(
                    {"type": "text", "text": prompt("runs/video-attachments", paths=paths)}
                )
                messages.append(message.model_copy(update={"content": content}))
        if not files:
            return None
        results = await self.backend.aupload_files(files)
        if len(results) != len(files) or any(result.error for result in results):
            raise RuntimeError("Could not stage video attachments in the run's sandbox")
        return {"messages": messages}
