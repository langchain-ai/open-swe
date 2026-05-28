"""Supported models and reasoning efforts surfaced in the profile editor."""

from __future__ import annotations

from typing import TypedDict


class ModelOption(TypedDict):
    id: str
    label: str
    efforts: list[str]
    default_effort: str


SUPPORTED_MODELS: list[ModelOption] = [
    {
        "id": "anthropic:claude-opus-4-8",
        "label": "Opus 4.8",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
    },
    {
        "id": "openai:gpt-5.5",
        "label": "GPT-5.5",
        "efforts": ["low", "medium", "high", "xhigh"],
        "default_effort": "xhigh",
    },
]

SUPPORTED_MODEL_IDS: frozenset[str] = frozenset(m["id"] for m in SUPPORTED_MODELS)

DEFAULT_MODEL_ID: str = "openai:gpt-5.5"
DEFAULT_MODEL_EFFORT: str = "medium"


def model_supports_effort(model_id: str, effort: str) -> bool:
    for m in SUPPORTED_MODELS:
        if m["id"] == model_id:
            return effort in m["efforts"]
    return False


def default_model_pair() -> tuple[str, str]:
    """Hardcoded fallback (model_id, reasoning_effort) used when no team default is set."""
    if DEFAULT_MODEL_ID in SUPPORTED_MODEL_IDS and model_supports_effort(
        DEFAULT_MODEL_ID, DEFAULT_MODEL_EFFORT
    ):
        return DEFAULT_MODEL_ID, DEFAULT_MODEL_EFFORT
    first = SUPPORTED_MODELS[0]
    return first["id"], first["default_effort"]
