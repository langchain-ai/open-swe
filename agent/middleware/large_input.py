import hashlib
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from xml.etree import ElementTree

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt

LARGE_INPUT_CHAR_THRESHOLD = 20_000


class LargeInputMiddleware(OpenSWEMiddleware):
    def __init__(self, backend: BackendProtocol) -> None:
        self.backend = backend
        self.uploaded: set[str] = set()

    async def _offload(self, text: str, work_dir: str) -> str:
        if len(text) <= LARGE_INPUT_CHAR_THRESHOLD:
            return text
        try:
            envelope = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return text
        if (
            envelope.tag != "input-message"
            or envelope.get("kind") != "human"
            or envelope.get("surface") not in {"web", "desktop"}
        ):
            return text
        body_element = envelope.find("content")
        body = body_element.text if body_element is not None else envelope.text
        if body is None:
            return text
        if body_element is None:
            body = body.removeprefix("\n").removesuffix("\n")
        if len(body) <= LARGE_INPUT_CHAR_THRESHOLD:
            return text
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        path = str(PurePosixPath(work_dir) / ".open-swe" / "attachments" / f"pasted-{digest}.txt")
        if path not in self.uploaded:
            results = await self.backend.aupload_files([(path, body.encode("utf-8"))])
            if len(results) != 1 or results[0].error:
                raise RuntimeError(f"Could not save oversized input to {path}: {results!r}")
            self.uploaded.add(path)
        reference = prompt("messages/large_input", path=path, char_count=len(body))
        if body_element is not None:
            body_element.text = reference
        else:
            envelope.text = f"\n{reference}\n"
        return ElementTree.tostring(envelope, encoding="unicode")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        work_dir = request.state.get("work_dir")
        if not isinstance(work_dir, str) or not work_dir:
            return await handler(request)
        messages = []
        for message in request.messages:
            if not isinstance(message, HumanMessage):
                messages.append(message)
                continue
            if isinstance(message.content, str):
                content = await self._offload(message.content, work_dir)
            else:
                content = [
                    {**block, "text": await self._offload(block["text"], work_dir)}
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    else block
                    for block in message.content
                ]
            messages.append(message.model_copy(update={"content": content}))
        return await handler(request.override(messages=messages))
