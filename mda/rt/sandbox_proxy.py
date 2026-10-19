"""Leave a managed sandbox's proxy rules to the Open SWE backend.

Before every command, MDA sets the box's proxy config to the one the project
declares, or to no rules at all when it declares none. The backend keeps the
thread's GitHub credentials and tool channel in those rules, so a command MDA
ran would drop them. For a sandbox declared without a proxy config, MDA now
leaves the rules as they are.
"""

import inspect
from collections.abc import Mapping
from typing import Final

from managed_deepagents._sandbox_proxy import SandboxProxyConfigSync  # noqa: PLC2701 - patched below
from managed_deepagents.sandbox import SandboxDefinition

_SYNC_PARAMETERS: Final = ("self", "sandbox", "name", "config")


def keep_backend_proxy_rules() -> None:
    """Patch MDA's per-command proxy sync to skip sandboxes that declare no proxy config."""
    sync = SandboxProxyConfigSync.sync
    if (
        not inspect.iscoroutinefunction(sync)
        or tuple(inspect.signature(sync).parameters) != _SYNC_PARAMETERS
    ):
        raise RuntimeError(
            "managed_deepagents changed SandboxProxyConfigSync.sync; "
            "update mda/rt/sandbox_proxy.py for the new version"
        )

    async def _sync(
        self: SandboxProxyConfigSync,
        sandbox: SandboxDefinition,
        name: str | None,
        config: Mapping[str, object] | None,
    ) -> None:
        if sandbox.options.get("proxy_config") is None:
            return
        await sync(self, sandbox, name, config)

    SandboxProxyConfigSync.sync = _sync
