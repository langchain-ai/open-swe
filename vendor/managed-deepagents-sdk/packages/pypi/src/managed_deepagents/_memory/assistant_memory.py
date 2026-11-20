"""Assistant-scoped Context Hub backend for ``/memories/assistant/``."""

from __future__ import annotations

from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path

from deepagents.backends import ContextHubBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol

from managed_deepagents._memory.user_memory import UserMemoryBackend

APPROVED_ASSISTANT: ContextVar[str | None] = ContextVar("mda_approved_assistant", default=None)


def assistant_memory_repo_handle(deploy_handle: str, assistant_id: str) -> str:
    """Return an opaque child-repo handle for one deployment and assistant."""
    digest = sha256(f"{deploy_handle}\0{assistant_id}".encode()).hexdigest()[:24]
    return f"{deploy_handle}--a--{digest}"


class AssistantMemoryBackend(UserMemoryBackend):
    """Reuse the run-local backend adapter with a trusted assistant binding."""

    def __init__(self, *, deploy_handle: str, filesystem_root: str | None) -> None:
        super().__init__(
            deploy_handle=deploy_handle,
            filesystem_root=filesystem_root,
            run_config=None,
        )

    def _repo_handle(self) -> str:
        assistant_id = APPROVED_ASSISTANT.get()
        if assistant_id is None:
            raise ValueError("Assistant memory requires a trusted runtime assistant identity")
        return assistant_memory_repo_handle(self._deploy_handle, assistant_id)

    def _open(self, handle: str) -> BackendProtocol:
        if self._filesystem_root is not None:
            root_dir = str(Path(self._filesystem_root) / "__assistants__" / handle)
            Path(root_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            return FilesystemBackend(root_dir=root_dir, virtual_mode=True)
        return ContextHubBackend(handle)
