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
        "id": "anthropic:claude-opus-4-7",
        "label": "Opus 4.7",
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


def model_supports_effort(model_id: str, effort: str) -> bool:
    for m in SUPPORTED_MODELS:
        if m["id"] == model_id:
            return effort in m["efforts"]
    return False


def default_model_pair() -> tuple[str, str]:
    """Hardcoded fallback (model_id, reasoning_effort) used when no team default is set."""
    for m in SUPPORTED_MODELS:
        if m["id"] == DEFAULT_MODEL_ID:
            return m["id"], m["default_effort"]
    first = SUPPORTED_MODELS[0]
    return first["id"], first["default_effort"]
