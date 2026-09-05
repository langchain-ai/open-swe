"""Re-attach thread media to messages right before the provider sees them."""

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, HumanMessage

from agent.dashboard.options import model_supports_images
from agent.media import MediaRef, download_media, media_refs_from_content

logger = logging.getLogger(__name__)


class MediaHydrationMiddleware(AgentMiddleware):
    """Swap envelope media references for the bytes they point at.

    State and checkpoints keep only the reference; the copy handed to the
    provider carries the image blocks. Hydration is content-addressed, so the
    request is byte-identical across turns and provider prompt caches stay warm.
    """

    def __init__(self, backend: SandboxBackendProtocol, *, model_id: str) -> None:
        self._backend = backend
        self._model_id = model_id
        self._supports_images = model_supports_images(model_id)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        messages = await self._hydrate(request.messages)
        if messages is None:
            return await handler(request)
        return await handler(request.override(messages=messages))

    async def _hydrate(self, messages: list[AnyMessage]) -> list[AnyMessage] | None:
        hydrated: list[AnyMessage] = []
        changed = False
        for message in messages:
            refs = (
                media_refs_from_content(message.content)
                if isinstance(message, HumanMessage)
                else []
            )
            if not refs:
                hydrated.append(message)
                continue
            changed = True
            blocks = [block for ref in refs for block in await self._blocks_for(ref)]
            content = message.content
            existing = content if isinstance(content, list) else [{"type": "text", "text": content}]
            hydrated.append(message.model_copy(update={"content": [*existing, *blocks]}))
        return hydrated if changed else None

    async def _blocks_for(self, ref: MediaRef) -> list[dict[str, Any]]:
        """A caption naming the file, then its pixels, so the pairing survives several attachments."""
        if not self._supports_images:
            return [_note(f"Attachment `{ref.path}` was withheld: {self._model_id} is text-only.")]
        data = await download_media(self._backend, ref.path)
        if data is None:
            return [_note(f"Attachment `{ref.path}` is no longer available in the sandbox.")]
        return [
            _note(f"Attachment `{ref.path}`:"),
            {
                "type": "image",
                "base64": base64.b64encode(data).decode("ascii"),
                "mime_type": ref.mime_type,
            },
        ]


def _note(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
