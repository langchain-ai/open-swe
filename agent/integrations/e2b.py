"""E2B sandbox provider."""

import asyncio

from deepagents.backends.protocol import SandboxBackendProtocol
from e2b import NotFoundException, Sandbox
from langchain_e2b import E2BSandbox

from ..config import e2b_api_key, e2b_template
from ..sandboxes.providers import SandboxGoneError, SandboxProvider

DEFAULT_E2B_SANDBOX_TIMEOUT = 60 * 60
# E2B's own default home, made explicit so the directory commands run in and the
# directory we hand out as the work dir cannot drift apart.
E2B_WORK_DIR = "/home/user"


class E2BProvider(SandboxProvider):
    """E2B sandboxes.

    Cannot boot from an Open SWE snapshot: E2B starts from a template built in
    its own registry and named once per deployment by ``E2B_TEMPLATE``.
    """

    async def connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        return await asyncio.to_thread(self._connect, sandbox_id)

    async def create(self, *, snapshot_id: str | None = None) -> SandboxBackendProtocol:
        if snapshot_id is not None:
            msg = f"E2B cannot boot from snapshot {snapshot_id!r}; its image is set by E2B_TEMPLATE"
            raise ValueError(msg)
        return await asyncio.to_thread(self._create)

    async def work_dir(self, backend: SandboxBackendProtocol) -> str:
        return E2B_WORK_DIR

    def _connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        try:
            sandbox = Sandbox.connect(
                sandbox_id,
                timeout=DEFAULT_E2B_SANDBOX_TIMEOUT,
                api_key=self._api_key(),
            )
        except NotFoundException as e:
            msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
            raise SandboxGoneError(msg) from e
        return self._backend(sandbox)

    def _create(self) -> SandboxBackendProtocol:
        api_key = self._api_key()
        template = e2b_template()
        if template:
            sandbox = Sandbox.create(
                template=template,
                timeout=DEFAULT_E2B_SANDBOX_TIMEOUT,
                api_key=api_key,
            )
        else:
            sandbox = Sandbox.create(timeout=DEFAULT_E2B_SANDBOX_TIMEOUT, api_key=api_key)
        return self._backend(sandbox)

    @staticmethod
    def _backend(sandbox: Sandbox) -> SandboxBackendProtocol:
        return E2BSandbox(sandbox=sandbox, workdir=E2B_WORK_DIR)

    @staticmethod
    def _api_key() -> str:
        api_key = e2b_api_key()
        if not api_key:
            raise ValueError("E2B_API_KEY environment variable is required")
        return api_key
