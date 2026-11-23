"""The model experiment's arms: which tier a person's thread on Auto runs on."""

import hashlib
from typing import Final, Literal

from openswe.run_config import RunConfig

ModelTier = Literal["balanced", "performance"]
# Adaptive's arms: the classifier picks the model, or Performance runs as its control.
AdaptiveArm = Literal["router", "performance"]


class RoutingArm:
    """Which arm of the model experiment a thread runs on, by a stable hash of its id."""

    # The share of experiment threads that run on Performance; the rest run on Balanced,
    # or with Adaptive routing on, through the classifier.
    PERFORMANCE_SHARE: Final = 0.5
    # Sources no person starts: automations and API runs.
    AUTOMATED_SOURCES: Final = frozenset({"schedule", "api"})

    @classmethod
    def on_performance(cls, thread_id: str) -> bool:
        """Whether the thread hashes into the Performance share."""
        digest = hashlib.sha256(thread_id.encode()).hexdigest()
        return int(digest[:8], 16) / float(0xFFFF_FFFF) < cls.PERFORMANCE_SHARE

    @classmethod
    def tier(cls, thread_id: str) -> ModelTier:
        """The tier the thread hashes into."""
        return "performance" if cls.on_performance(thread_id) else "balanced"

    @classmethod
    def adaptive(cls, thread_id: str) -> AdaptiveArm:
        """The Adaptive arm the thread hashes into."""
        return "performance" if cls.on_performance(thread_id) else "router"

    @classmethod
    def automated(cls, cfg: RunConfig) -> bool:
        """Whether an automation, an API client or a Slack bot started the run."""
        return (
            cfg.source in cls.AUTOMATED_SOURCES
            or cfg.schedule_id is not None
            or bool(cfg.slack_thread and cfg.slack_thread.triggering_bot_id)
        )
