"""This Mac threads, whose sandbox was their owner's machine and which nothing reaches now.

Kept free of the agent stack: the thread index, which the web app imports, asks it too.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

# Set on a This Mac thread whose next run moved it to a cloud sandbox, to tell its agent.
MOVED_OFF_MAC_KEY: Final = "moved_off_mac"


class MacSandbox:
    """A ``sandbox_id`` that named its owner's machine: ``bridge:`` and the bridge's id."""

    PREFIX: Final = "bridge:"
    # The metadata that bound a thread to its owner's machine, cleared when it moves.
    _BINDING: Final = (
        "sandbox_id",
        "sandbox_base_proxy_config",
        "sandbox_kind",
        "sandbox_bridge_client",
    )

    @staticmethod
    def names(sandbox_id: object) -> bool:
        """Whether ``sandbox_id`` names a This Mac thread's machine."""
        return isinstance(sandbox_id, str) and sandbox_id.startswith(MacSandbox.PREFIX)

    @staticmethod
    def reachable_id(metadata: Mapping[str, object]) -> str | None:
        """The sandbox a thread's metadata names, or ``None`` when there is none to reach.

        A This Mac thread has none: LangSmith never had its sandbox, so connecting
        would fail on every try.
        """
        sandbox_id = metadata.get("sandbox_id")
        if not isinstance(sandbox_id, str) or not sandbox_id or MacSandbox.names(sandbox_id):
            return None
        return sandbox_id

    @staticmethod
    def move_update() -> dict[str, JsonValue]:
        """The metadata update that moves a This Mac thread to a new cloud sandbox."""
        return {**dict.fromkeys(MacSandbox._BINDING), MOVED_OFF_MAC_KEY: True}
