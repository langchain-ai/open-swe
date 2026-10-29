"""Immutable, non-secret inputs to an agent's per-turn preparation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from runtime_api.catalog import InboxSpec, ModelSpec

from openswe.bridge.constants import BridgeClient
from openswe.dashboard.options import ModelOption


class AgentPreparation(BaseModel):
    """Versioned preparation inputs carried only by the backend-signed run settings."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    repo_instructions: str | None = None
    title_model: ModelSpec
    use_gateway: bool
    routing_defaults: dict[str, tuple[str, str | None]]
    routing_mode: Literal["auto", "fast"] | None = None
    requested_models: dict[str, ModelOption] | None = None
    saved_requested_model: str | None = None
    bridge_client: BridgeClient | None = None
    tool_catalog_note: str
    inbox: InboxSpec | None = None
    reply_surface: Literal["slack", "web"]
    review_guide: bool
    cli_result: bool
    image_fallback: bool

    def bounded(self) -> AgentPreparation | None:
        """Keep large repository instructions out of callback authorization headers."""
        return self if len(self.model_dump_json().encode()) <= 4096 else None
