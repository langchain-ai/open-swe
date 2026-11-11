"""The model routing experiment's arms: which threads routing picks the model for."""

import hashlib
from typing import Final

from openswe.dashboard.workspace_settings_cache import cached_workspace_settings


class RoutingArm:
    """Which arm of the routing experiment a thread is in, by a stable hash of its id."""

    # The share of threads the experiment routes; the rest run on the model their person picked.
    ROUTED_SHARE: Final = 0.5

    @classmethod
    def hashes_routed(cls, thread_id: str) -> bool:
        """Whether the thread hashes into the routed arm."""
        digest = hashlib.sha256(thread_id.encode()).hexdigest()
        return int(digest[:8], 16) / float(0xFFFF_FFFF) < cls.ROUTED_SHARE

    @classmethod
    async def routes(cls, thread_id: str, workspace: str | None) -> bool:
        """Whether routing picks the thread's model, ignoring any model a person picked."""
        settings = await cached_workspace_settings(workspace)
        return settings.model_routing_enabled and cls.hashes_routed(thread_id)
