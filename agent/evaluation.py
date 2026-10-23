"""Contracts shared by the coding graph and its offline evaluation target."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompactionSummaryPrompt(StrEnum):
    """Summary prompt variant used by a compaction experiment arm."""

    DEFAULT = "default"
    STRUCTURED = "structured"


class CompactionConfig(BaseModel):
    """One compaction experiment arm.

    At most one trigger dimension may be set; leaving all of them unset keeps
    the model-derived default. ``trigger_tokens`` works for models without a
    context-window profile and is therefore the portable choice.
    """

    model_config = ConfigDict(extra="forbid")

    trigger_tokens: int | None = Field(default=None, gt=0)
    trigger_messages: int | None = Field(default=None, gt=0)
    trigger_fraction: float | None = Field(default=None, gt=0, le=1)
    summary_prompt: CompactionSummaryPrompt = CompactionSummaryPrompt.DEFAULT

    @model_validator(mode="after")
    def single_trigger(self) -> CompactionConfig:
        configured = [
            value
            for value in (
                self.trigger_tokens,
                self.trigger_messages,
                self.trigger_fraction,
            )
            if value is not None
        ]
        if len(configured) > 1:
            raise ValueError("At most one compaction trigger dimension may be set")
        return self


class AgentEvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_commit: str = Field(pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
    checkout_dir: str = Field(pattern=r"^eval-[0-9a-f]{16}$")
    compaction: CompactionConfig | None = None


class AgentEvaluationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["collected", "collection_error"]
    patch: str = ""
    error: str = ""
