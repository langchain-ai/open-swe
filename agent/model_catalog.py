"""Runtime models.dev metadata for the installed provider adapters."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google_genai",
    "fireworks-ai": "fireworks",
}
RESOURCE = Path(__file__).parent / "resources" / "models-dev.json"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "open-swe" / "models-dev.json"
)


class ReasoningOption(BaseModel):
    type: str
    values: list[str] = Field(default_factory=list)


class Limits(BaseModel):
    context: int = Field(ge=0)
    input: int | None = Field(default=None, ge=0)
    output: int = Field(ge=0)


class Modalities(BaseModel):
    input: list[str]
    output: list[str]


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    tool_call: bool
    reasoning: bool
    reasoning_options: list[ReasoningOption] = Field(default_factory=list)
    modalities: Modalities
    limit: Limits
    release_date: str = ""
    status: Literal["alpha", "beta", "deprecated"] | None = None

    def efforts(self) -> list[str]:
        return next(
            (option.values for option in self.reasoning_options if option.type == "effort"),
            [] if self.reasoning else ["none"],
        )


class CatalogProvider(BaseModel):
    models: dict[str, CatalogModel]


def parse_catalog(payload: object) -> dict[str, CatalogModel]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid models.dev catalog")
    result: dict[str, CatalogModel] = {}
    for source, provider in PROVIDERS.items():
        models = CatalogProvider.model_validate(payload[source]).models
        compatible = {
            f"{provider}:{model.id}": model
            for model in models.values()
            if model.tool_call
            and "text" in model.modalities.output
            and model.efforts()
            and not (provider == "anthropic" and model.id.startswith("claude-opus-4-5"))
            and model.limit.context > 0
            and model.limit.output > 0
        }
        if not compatible:
            raise ValueError(f"No compatible models for {source}")
        result.update(compatible)
    return dict(sorted(result.items(), key=lambda item: item[1].release_date, reverse=True))


CATALOG = parse_catalog(json.loads(RESOURCE.read_text()))
_last_refresh = float("-inf")
_lock = asyncio.Lock()


async def refresh_catalog() -> bool:
    global _last_refresh
    if time.monotonic() - _last_refresh < 3600:
        return False
    async with _lock:
        if time.monotonic() - _last_refresh < 3600:
            return False
        _last_refresh = time.monotonic()
        try:
            async with httpx2.AsyncClient(timeout=15) as client:
                response = await client.get("https://models.dev/api.json")
                response.raise_for_status()
                payload = response.json()
            models = parse_catalog(payload)
        except httpx2.HTTPError, ValueError, KeyError:
            logger.warning(
                "Model catalog refresh failed; retaining the last snapshot", exc_info=True
            )
            return False
        CATALOG.clear()
        CATALOG.update(models)
        from agent.dashboard.options import update_catalog_options

        update_catalog_options()
        try:
            await asyncio.to_thread(save_snapshot, payload)
        except OSError:
            logger.warning("Could not persist model catalog snapshot", exc_info=True)
        return True


def save_snapshot(payload: object) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(CACHE)


try:
    if CACHE.exists():
        cached = parse_catalog(json.loads(CACHE.read_text()))
        CATALOG.clear()
        CATALOG.update(cached)
except OSError, ValueError, KeyError:
    logger.warning("Could not load cached model catalog", exc_info=True)
    CATALOG.update(parse_catalog(json.loads(RESOURCE.read_text())))
