from agent.dashboard.agent_overrides import profile_disable_subagents


def test_profile_disable_subagents_defaults_false() -> None:
    assert profile_disable_subagents(None) is False
    assert profile_disable_subagents({}) is False
    assert profile_disable_subagents({"disable_subagents": "true"}) is False


def test_profile_disable_subagents_reads_true() -> None:
    assert profile_disable_subagents({"disable_subagents": True}) is True
    assert profile_disable_subagents({"disable_subagents": False}) is False
