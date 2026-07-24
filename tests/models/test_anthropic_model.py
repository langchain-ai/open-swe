import pytest

from agent.dashboard.options import SUPPORTED_MODELS, provider_fallback_pair
from agent.utils.model import provider_model_kwargs

OPUS_5_ID = "anthropic:claude-opus-5"
SONNET_5_ID = "anthropic:claude-sonnet-5"
FABLE_5_ID = "anthropic:claude-fable-5"


def test_opus_5_is_supported_with_documented_efforts() -> None:
    opus = next(m for m in SUPPORTED_MODELS if m["id"] == OPUS_5_ID)
    assert opus.get("label") == "Opus 5"
    assert opus.get("efforts") == ["low", "medium", "high", "xhigh", "max"]
    assert opus.get("default_effort") == "high"
    assert opus["supports_images"] is True


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_opus_5_efforts_map_to_anthropic_kwargs(effort: str) -> None:
    kwargs = provider_model_kwargs(OPUS_5_ID, effort, max_tokens=64_000)
    assert kwargs.get("max_tokens") == 64_000
    assert kwargs.get("effort") == effort
    assert kwargs.get("thinking") == {"type": "adaptive", "display": "summarized"}


def test_sonnet_5_is_supported_with_documented_efforts() -> None:
    sonnet = next(m for m in SUPPORTED_MODELS if m["id"] == SONNET_5_ID)
    assert sonnet.get("label") == "Sonnet 5"
    assert sonnet.get("efforts") == ["low", "medium", "high", "xhigh", "max"]
    assert sonnet.get("default_effort") == "high"
    assert sonnet["supports_images"] is True


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_sonnet_5_efforts_map_to_anthropic_kwargs(effort: str) -> None:
    kwargs = provider_model_kwargs(SONNET_5_ID, effort, max_tokens=16_000)
    assert kwargs.get("max_tokens") == 16_000
    assert kwargs.get("effort") == effort
    assert kwargs.get("thinking") == {"type": "adaptive", "display": "summarized"}


def test_sonnet_46_fallback_uses_sonnet_5() -> None:
    assert provider_fallback_pair("anthropic:claude-sonnet-4-6", "xhigh") == (
        SONNET_5_ID,
        "xhigh",
    )


def test_opus_fallback_stays_on_opus_family() -> None:
    assert provider_fallback_pair("anthropic:claude-opus-4-8", "xhigh") == (
        OPUS_5_ID,
        "xhigh",
    )


def test_fable_5_is_supported_with_documented_efforts() -> None:
    fable = next(m for m in SUPPORTED_MODELS if m["id"] == FABLE_5_ID)
    assert fable.get("label") == "Fable 5"
    assert fable.get("efforts") == ["low", "medium", "high", "xhigh", "max"]
    assert fable.get("default_effort") == "high"
    assert fable["supports_images"] is True


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_fable_5_efforts_map_to_anthropic_kwargs(effort: str) -> None:
    kwargs = provider_model_kwargs(FABLE_5_ID, effort, max_tokens=16_000)
    assert kwargs.get("max_tokens") == 16_000
    assert kwargs.get("effort") == effort
    assert kwargs.get("thinking") == {"type": "adaptive", "display": "summarized"}
