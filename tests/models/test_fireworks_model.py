import pytest

from agent.dashboard.options import SUPPORTED_MODELS
from agent.utils.model import (
    fallback_model_id_for,
    fireworks_reasoning_effort_for,
    provider_model_kwargs,
)


def test_fireworks_reasoning_effort_maps_effort() -> None:
    for effort in ("none", "low", "medium", "high", "xhigh", "max"):
        assert fireworks_reasoning_effort_for(effort) == effort
    assert fireworks_reasoning_effort_for("bogus") is None
    assert fireworks_reasoning_effort_for(None) is None


def test_provider_model_kwargs_for_fireworks() -> None:
    kwargs = provider_model_kwargs(
        "fireworks:accounts/fireworks/models/kimi-k2p7-code",
        "high",
        max_tokens=16_000,
    )
    assert kwargs.get("max_tokens") == 16_000
    assert kwargs.get("model_kwargs") == {"reasoning_effort": "high"}


def test_kimi_k2p7_is_supported() -> None:
    kimi_k2p7 = next(
        (m for m in SUPPORTED_MODELS if m["id"].endswith("kimi-k2p7-code")),
        None,
    )
    assert kimi_k2p7 is not None
    assert kimi_k2p7.get("efforts") == ["low", "medium", "high"]
    assert "none" not in kimi_k2p7.get("efforts", [])
    assert kimi_k2p7.get("default_effort") == "high"
    model_id = kimi_k2p7.get("id")
    assert isinstance(model_id, str)
    kwargs = provider_model_kwargs(model_id, "high", max_tokens=16_000)
    assert kwargs.get("model_kwargs") == {"reasoning_effort": "high"}


def test_provider_model_kwargs_for_fireworks_none_disables_reasoning() -> None:
    kwargs = provider_model_kwargs(
        "fireworks:accounts/fireworks/models/deepseek-v4-pro",
        "none",
        max_tokens=16_000,
    )
    assert kwargs.get("model_kwargs") == {"reasoning_effort": "none"}


def test_provider_model_kwargs_for_fireworks_unknown_effort_omits_reasoning() -> None:
    kwargs = provider_model_kwargs(
        "fireworks:accounts/fireworks/models/glm-5p1",
        "bogus",
        max_tokens=16_000,
    )
    assert "model_kwargs" not in kwargs


def test_fireworks_has_no_cross_provider_fallback() -> None:
    assert fallback_model_id_for("fireworks:accounts/fireworks/models/deepseek-v4-pro") is None


@pytest.mark.parametrize(
    ("model_id", "effort"),
    [(m["id"], effort) for m in SUPPORTED_MODELS for effort in m["efforts"]],
)
def test_every_supported_effort_translates_to_a_reasoning_kwarg(model_id: str, effort: str) -> None:
    """Each effort surfaced in the UI must map to a provider reasoning param."""
    kwargs = provider_model_kwargs(model_id, effort, max_tokens=16_000)
    assert set(kwargs) - {"max_tokens"}, (
        f"{model_id} effort {effort!r} did not produce a reasoning kwarg"
    )
