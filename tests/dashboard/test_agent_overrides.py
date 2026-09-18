import pytest
from pydantic import ValidationError

from agent.dashboard.agent_overrides import profile_disable_subagents
from agent.dashboard.profiles import parse_profile


def test_profile_disable_subagents_defaults_false() -> None:
    assert profile_disable_subagents(None) is False
    assert profile_disable_subagents({}) is False
    with pytest.raises(ValidationError):
        parse_profile({"disable_subagents": "true"})


def test_profile_disable_subagents_reads_true() -> None:
    assert profile_disable_subagents({"disable_subagents": True}) is True
    assert profile_disable_subagents({"disable_subagents": False}) is False
