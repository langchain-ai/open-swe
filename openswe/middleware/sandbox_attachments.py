"""Hand the model sandbox paths for attached files instead of their bytes.

Placed inside ``TranscriptMiddleware`` so the transcript still records each
attachment's bytes from the request, and ahead of every model-specific
middleware so no provider ever receives them.
"""

import logging
from collections.abc import Awaitable, Callable

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, HumanMessage

from openswe.attachments import SandboxAttachment, message_attachments
from openswe.middleware.trace import OpenSWEMiddleware

logger = logging.getLogger(__name__)


class SandboxAttachmentsMiddleware(OpenSWEMiddleware):
    """Write each human message's attachments into the sandbox, once per run."""

    def __init__(self, backend: BackendProtocol) -> None:
        super().__init__()
        self._backend = backend
        self._written: set[str] = set()

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        found = {
            index: attachments
            for index, message in enumerate(request.messages)
            if isinstance(message, HumanMessage) and (attachments := message_attachments(message))
        }
        if not found:
            return await handler(request)
        errors = await self._write([a for attachments in found.values() for a in attachments])
        messages: list[AnyMessage] = list(request.messages)
        for index, attachments in found.items():
            content = list(messages[index].content)
            for attachment in attachments:
                error = errors.get(attachment.sandbox_path)
                content[attachment.position] = attachment.reference(
                    error=f"could not be written to the sandbox ({error})" if error else None
                )
            messages[index] = messages[index].model_copy(update={"content": content})
        return await handler(request.override(messages=messages))

    async def _write(self, attachments: list[SandboxAttachment]) -> dict[str, str]:
        """Write what this run has not written yet; the errors, by sandbox path."""
        pending = {a.sandbox_path: a for a in attachments if a.sandbox_path not in self._written}
        if not pending:
            return {}
        try:
            responses = await self._backend.aupload_files(
                [(path, a.attachment.data) for path, a in pending.items()]
            )
            results = [response.error for response in responses]
        except Exception:
            logger.warning("Could not write attachments into the sandbox", exc_info=True)
            results = ["upload_failed"] * len(pending)
        errors: dict[str, str] = {}
        for path, error in zip(pending, results, strict=True):
            if error:
                errors[path] = str(error)
                logger.warning(
                    "Could not write an attachment into the sandbox",
                    extra={"attachment_path": path, "attachment_error": str(error)},
                )
            else:
                self._written.add(path)
        return errors
