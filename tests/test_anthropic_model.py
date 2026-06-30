import pytest

from agent.dashboard.options import SUPPORTED_MODELS, provider_fallback_pair
from agent.utils.model import provider_model_kwargs

SONNET_5_ID = "anthropic:claude-sonnet-5"


def test_sonnet_5_is_supported_with_documented_efforts() -> None:
    sonnet = next(m for m in SUPPORTED_MODELS if m["id"] == SONNET_5_ID)
    assert sonnet["label"] == "Sonnet 5"
    assert sonnet["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert sonnet["default_effort"] == "high"
    assert sonnet["supports_images"] is True


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_sonnet_5_efforts_map_to_anthropic_kwargs(effort: str) -> None:
    kwargs = provider_model_kwargs(SONNET_5_ID, effort, max_tokens=16_000)
    assert kwargs["max_tokens"] == 16_000
    assert kwargs["effort"] == effort
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_sonnet_46_fallback_uses_sonnet_5() -> None:
    assert provider_fallback_pair("anthropic:claude-sonnet-4-6", "xhigh") == (
        SONNET_5_ID,
        "xhigh",
    )


def test_opus_fallback_stays_on_opus_family() -> None:
    assert provider_fallback_pair("anthropic:claude-opus-4-7", "xhigh") == (
        "anthropic:claude-opus-4-8",
        "xhigh",
    )
