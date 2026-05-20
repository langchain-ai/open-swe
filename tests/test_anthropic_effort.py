from __future__ import annotations

import pytest

from agent.server import _anthropic_effort_for, _anthropic_thinking_for
from agent.utils.model import make_model


def test_anthropic_uses_adaptive_thinking_and_effort() -> None:
    assert _anthropic_thinking_for("high") == {"type": "adaptive"}
    assert _anthropic_effort_for("high") == "high"


def test_anthropic_ignores_unknown_effort() -> None:
    assert _anthropic_thinking_for("unknown") is None
    assert _anthropic_effort_for("unknown") is None


def test_make_model_rejects_legacy_enabled_thinking() -> None:
    # Regression guard: the legacy {"type": "enabled"} shape is no longer
    # accepted by current Claude models and used to surface as a BadRequest
    # on the first model call, producing an empty root run.
    with pytest.raises(ValueError, match="thinking"):
        make_model("anthropic:claude-opus-4-5", thinking={"type": "enabled"})


def test_make_model_rejects_unknown_thinking_type() -> None:
    with pytest.raises(ValueError, match="thinking"):
        make_model("anthropic:claude-opus-4-5", thinking={"type": "bogus"})
