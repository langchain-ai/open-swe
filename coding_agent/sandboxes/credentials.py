"""Credentials a platform installs on a sandbox so the agent can reach its services."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class InstalledCredentials(Protocol):
    """Credentials already on a sandbox, not yet attributed to a thread."""

    @property
    def base_config(self) -> dict[str, Any] | None:
        """Config to persist with the thread so a reconnect installs the same one."""

    async def bind(self, thread_id: str | None) -> None:
        """Record these as the thread's credentials, so a refresh keeps their scope."""


@runtime_checkable
class SandboxCredentials(Protocol):
    async def install(
        self,
        sandbox_id: str,
        *,
        thread_id: str | None = None,
        base_proxy_config: dict[str, Any] | None = None,
    ) -> InstalledCredentials:
        """Install credentials on the sandbox.

        ``thread_id`` only times the work; the result is attributed to a thread
        by ``InstalledCredentials.bind``, which callers defer until the thread is
        actually bound to the sandbox.
        """

    async def recorded_base_config(self, thread_id: str | None) -> dict[str, Any] | None:
        """The config last installed for the thread, for reconnects."""


@dataclass(frozen=True, slots=True)
class NoCredentials:
    """Nothing to install and nothing to record."""

    @property
    def base_config(self) -> dict[str, Any] | None:
        return None

    async def bind(self, thread_id: str | None) -> None:
        return

    async def install(
        self,
        sandbox_id: str,
        *,
        thread_id: str | None = None,
        base_proxy_config: dict[str, Any] | None = None,
    ) -> InstalledCredentials:
        return self

    async def recorded_base_config(self, thread_id: str | None) -> dict[str, Any] | None:
        return None
