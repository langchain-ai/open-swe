from __future__ import annotations

from agent.server import _anthropic_effort_for, _anthropic_thinking_for


def test_opus_47_uses_adaptive_thinking_and_effort() -> None:
    assert _anthropic_thinking_for("anthropic:claude-opus-4-7", "high") == {"type": "adaptive"}
    assert _anthropic_effort_for("anthropic:claude-opus-4-7", "high") == "high"


def test_pre_opus_47_keeps_budgeted_thinking() -> None:
    assert _anthropic_thinking_for("anthropic:claude-opus-4-5", "high") == {
        "type": "enabled",
        "budget_tokens": 12_000,
    }
    assert _anthropic_effort_for("anthropic:claude-opus-4-5", "high") is None
