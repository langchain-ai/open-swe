from __future__ import annotations

from agent.utils.model import anthropic_effort_for, anthropic_thinking_for


def test_anthropic_uses_adaptive_thinking_and_effort() -> None:
    assert anthropic_thinking_for("high") == {"type": "adaptive"}
    assert anthropic_effort_for("high") == "high"


def test_anthropic_ignores_unknown_effort() -> None:
    assert anthropic_thinking_for("unknown") is None
    assert anthropic_effort_for("unknown") is None
