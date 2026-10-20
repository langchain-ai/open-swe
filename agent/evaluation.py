"""Contracts shared by the coding graph and its offline evaluation target."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentEvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_commit: str = Field(pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
    checkout_dir: str = Field(pattern=r"^eval-[0-9a-f]{16}$")


class AgentEvaluationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["collected", "collection_error"]
    patch: str = ""
    error: str = ""
