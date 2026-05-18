from __future__ import annotations

from agent.server import _anthropic_effort_for, _anthropic_thinking_for


def test_anthropic_uses_adaptive_thinking_and_effort() -> None:
    assert _anthropic_thinking_for("high") == {"type": "adaptive"}
    assert _anthropic_effort_for("high") == "high"


def test_anthropic_ignores_unknown_effort() -> None:
    assert _anthropic_thinking_for("unknown") is None
    assert _anthropic_effort_for("unknown") is None
