"""The sandbox provider contract, the provider registry, and the errors runs recover from.

One provider class per platform, selected by ``SANDBOX_TYPE``. Everything the
rest of the codebase needs to know about a platform — how to reach a sandbox,
whether its traffic goes through a GitHub proxy, whether it can boot from a
snapshot, where it keeps a writable tree — is answered by the provider rather
than by branching on the provider's name.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from importlib import import_module
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict

from ..config import sandbox_provider

if TYPE_CHECKING:
    # Kept out of the runtime import graph: the webapp reaches this module for
    # provider capabilities and must not pull the agent stack in with it.
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


class SandboxResources(TypedDict, total=False):
    """VM sizing for a new sandbox; a missing key means the platform default."""

    mem_bytes: int
    vcpus: int
    fs_capacity_bytes: int


class SandboxGoneError(RuntimeError):
    """The sandbox a thread is bound to no longer exists.

    Distinct from a sandbox that is merely unreachable: a deleted one holds no
    working tree, so callers recreate instead of failing the run.
    """


class SandboxUnreachableError(RuntimeError):
    """The thread's sandbox did not answer this run.

    Says nothing about whether it will answer the next one — a later run
    reconnects to the same id and may well succeed. It is never resolved by
    creating a replacement: the sandbox holds the agent's only copy of its
    working tree, so a fresh one would discard uncommitted work while the agent
    carried on believing it was still there.
    """

    def __init__(self, thread_id: str, sandbox_id: str | None, cause: str) -> None:
        self.thread_id = thread_id
        self.sandbox_id = sandbox_id
        super().__init__(
            f"Sandbox {sandbox_id or '<unknown>'} for thread {thread_id} is unreachable: {cause}"
        )


class SandboxProvider(ABC):
    """One sandbox platform, as the rest of Open SWE sees it.

    Intentionally has no delete. A sandbox holds the agent's only copy of its
    working tree, and the thread metadata read fails open to "no sandbox", so a
    delete keyed off it can destroy a live box. Reclamation is the platform's
    job, via the idle TTL and delete-after-stop set at create time.
    """

    # Whether the platform fronts the sandbox's GitHub traffic with a proxy we
    # configure with a token, instead of the sandbox reaching GitHub directly.
    uses_github_proxy: ClassVar[bool] = False

    # Whether `create` can boot a sandbox from an Open SWE snapshot id (the ids
    # environments and repo snapshot builds produce).
    supports_snapshots: ClassVar[bool] = False

    @abstractmethod
    async def connect(self, sandbox_id: str) -> "SandboxBackendProtocol":
        """Reconnect to the sandbox ``sandbox_id`` names.

        Raise ``SandboxGoneError`` when — and only when — the platform reports
        that the sandbox does not exist. The lifecycle replaces a gone sandbox
        and refuses to replace a merely unreachable one, so a not-found that
        arrives as some other error type bricks the thread permanently.
        """

    @abstractmethod
    async def create(
        self,
        *,
        snapshot_id: str | None = None,
        resources: SandboxResources | None = None,
        create_params: Mapping[str, Any] | None = None,
    ) -> "SandboxBackendProtocol":
        """Provision a fresh sandbox, booting it from ``snapshot_id`` when given.

        ``resources`` sizes the VM and ``create_params`` are extra fields for the
        platform's create request, both as an environment configured them. A
        provider that cannot honour a non-empty one raises — as it does for a
        snapshot it cannot boot from — rather than silently starting a sandbox
        that is not what the caller asked for.
        """

    @staticmethod
    def _reject_sizing(
        platform: str,
        resources: SandboxResources | None,
        create_params: Mapping[str, Any] | None,
    ) -> None:
        if resources or create_params:
            msg = (
                f"{platform} sandboxes are sized by the platform configuration; an environment's "
                "resources and create_params are not supported"
            )
            raise ValueError(msg)

    def proxy_config(self, create_params: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """The proxy settings a sandbox created with ``create_params`` carries.

        Only meaningful where ``uses_github_proxy``: the GitHub auth rules are
        layered on top of these, so they have to be re-sent with every token.
        """
        return None

    def validate_startup_config(self) -> None:  # noqa: B027 - optional: most providers read no env
        """Reject env-var configuration this provider cannot run with, at server boot."""

    async def work_dir(self, backend: "SandboxBackendProtocol") -> str | None:
        """The writable directory this platform gives its sandboxes.

        ``None`` when the platform has nothing to say, which leaves the caller
        to ask the sandbox's own shell.
        """
        return None


_PROVIDERS: dict[str, tuple[str, str]] = {
    "langsmith": ("agent.integrations.langsmith", "LangSmithProvider"),
    "daytona": ("agent.integrations.daytona", "DaytonaProvider"),
    "modal": ("agent.integrations.modal", "ModalProvider"),
    "runloop": ("agent.integrations.runloop", "RunloopProvider"),
    "e2b": ("agent.integrations.e2b", "E2BProvider"),
    "local": ("agent.integrations.local", "LocalProvider"),
}


def current_sandbox_provider() -> SandboxProvider:
    """The provider ``SANDBOX_TYPE`` selects.

    Imported on demand: a deployment installs one platform's SDK, and importing
    the other five would fail.
    """
    name = sandbox_provider()
    location = _PROVIDERS.get(name)
    if location is None:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Invalid sandbox type: {name}. Supported types: {supported}")
    module_name, class_name = location
    provider_class = getattr(import_module(module_name), class_name)
    return provider_class()


def sandbox_provider_uses_proxy() -> bool:
    return current_sandbox_provider().uses_github_proxy


def sandbox_provider_supports_snapshots() -> bool:
    return current_sandbox_provider().supports_snapshots


async def create_sandbox(
    sandbox_id: str | None = None,
    *,
    snapshot_id: str | None = None,
    resources: SandboxResources | None = None,
    create_params: Mapping[str, Any] | None = None,
) -> "SandboxBackendProtocol":
    """Reconnect to ``sandbox_id``, or provision a new sandbox from ``snapshot_id``.

    The two are alternatives, not a pair: a snapshot (like the sizing and create
    params) only ever seeds a sandbox being created, so passing both is a caller
    bug rather than a preference.
    """
    if sandbox_id and (snapshot_id or resources or create_params):
        raise ValueError("snapshot_id seeds a new sandbox; it cannot be applied to sandbox_id")
    provider = current_sandbox_provider()
    if sandbox_id:
        return await provider.connect(sandbox_id)
    return await provider.create(
        snapshot_id=snapshot_id, resources=resources, create_params=create_params
    )


def validate_sandbox_startup_config() -> None:
    """Validate the configured provider's env vars at server startup.

    Called from the FastAPI lifespan hook so errors surface at boot rather than
    on the first sandbox creation.
    """
    current_sandbox_provider().validate_startup_config()
