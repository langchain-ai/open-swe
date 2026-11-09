"""The model each call goes to, as both the backend and the MDA runtime pick it."""

import pytest
from runtime_api.catalog import ModelSpec, RunModels
from runtime_api.models import chosen_model, fallback_spec, model_for, model_spec

_FALLBACK = "anthropic:claude-sonnet-5-5"
_MAIN = model_spec("anthropic:claude-opus-5-5", {"max_tokens": 64_000})
_FAST = model_spec("anthropic:claude-haiku-5-5", {"max_tokens": 64_000})


def _models() -> RunModels:
    return RunModels(
        model=_MAIN,
        subagent_model=_MAIN,
        use_gateway=False,
        routes={"fast": _FAST},
        fallback_model_id=_FALLBACK,
    )


def test_a_model_falls_back_to_the_other_provider_reading_images_as_it_does() -> None:
    fallback = fallback_spec("anthropic:claude-opus-5-5")

    assert fallback is not None and fallback.model_id == "openai:gpt-6.1-sol"
    assert fallback.reads_images is True
    assert fallback.kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert fallback_spec("google_genai:gemini-4-argon") is None


def test_the_deployments_fallback_wins_but_never_the_model_itself() -> None:
    chosen = chosen_model("openai:gpt-6.1-sol", "high", fallback_model_id=_FALLBACK)

    assert chosen.fallback is not None and chosen.fallback.model_id == _FALLBACK
    assert fallback_spec("openai:gpt-6.1-sol", fallback_model_id="openai:gpt-6.1-sol") is None


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        ({}, _MAIN),
        ({"model_route": "fast"}, _FAST),
        ({"model_route": "fast_alt"}, _FAST),
        ({"model_route": "default"}, _MAIN),
        ({"selected_model_id": "anthropic:claude-opus-5-5"}, _MAIN),
        (
            {"selected_model_id": "openai:gpt-6.1-sol", "selected_effort": "low"},
            chosen_model("openai:gpt-6.1-sol", "low", fallback_model_id=_FALLBACK),
        ),
        (
            {"requested_model": "openai:gpt-6-luna", "model_route": "fast"},
            chosen_model("openai:gpt-6-luna", None, fallback_model_id=_FALLBACK),
        ),
    ],
)
def test_the_next_call_goes_to_the_model_asked_for_then_the_route_then_the_runs(
    held: dict[str, object], expected: ModelSpec
) -> None:
    assert model_for(_models(), held) == expected
