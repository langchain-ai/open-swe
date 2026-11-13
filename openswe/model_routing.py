"""The model experiment's arms: which tier a person's thread on Auto runs on."""

import hashlib
from typing import Final, Literal

from openswe.run_config import RunConfig

ModelTier = Literal["balanced", "performance"]


class RoutingArm:
    """Which tier of the model experiment a thread runs on, by a stable hash of its id."""

    # The share of experiment threads that run on Performance; the rest run on Balanced.
    PERFORMANCE_SHARE: Final = 0.5
    # Sources no person starts: workspace automations and API runs.
    AUTOMATED_SOURCES: Final = frozenset({"schedule", "api"})

    @classmethod
    def tier(cls, thread_id: str) -> ModelTier:
        """The tier the thread hashes into."""
        digest = hashlib.sha256(thread_id.encode()).hexdigest()
        share = int(digest[:8], 16) / float(0xFFFF_FFFF)
        return "performance" if share < cls.PERFORMANCE_SHARE else "balanced"

    @classmethod
    def automated(cls, cfg: RunConfig) -> bool:
        """Whether an automation, an API client or a Slack bot started the run."""
        return (
            cfg.source in cls.AUTOMATED_SOURCES
            or cfg.schedule_id is not None
            or bool(cfg.slack_thread and cfg.slack_thread.triggering_bot_id)
        )
