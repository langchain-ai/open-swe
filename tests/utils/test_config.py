"""The environment registry: precedence, aliases, typed getters, and the no-bypass rule."""

import re
from pathlib import Path

import pytest

from agent.config import ENV, Registry


def test_current_name_wins_over_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "standard")
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "legacy")

    assert ENV.LANGSMITH_API_KEY.get() == "standard"
    assert ENV.LANGSMITH_API_KEY.source() == "LANGSMITH_API_KEY"


def test_alias_is_read_when_current_name_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "legacy")

    assert ENV.LANGSMITH_API_KEY.optional() == "legacy"
    assert ENV.LANGSMITH_API_KEY.source() == "LANGSMITH_API_KEY_PROD"


def test_blank_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "   ")

    assert not ENV.SANDBOX_TYPE.is_set()
    assert ENV.SANDBOX_TYPE.get() == "langsmith"


def test_get_argument_overrides_declared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_TYPE", raising=False)

    assert ENV.SANDBOX_TYPE.get("local") == "local"
    assert ENV.SANDBOX_TYPE.optional() is None


def test_get_returns_empty_string_without_any_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    assert ENV.SLACK_BOT_TOKEN.get() == ""


def test_require_raises_for_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)

    with pytest.raises(KeyError):
        ENV.LANGCHAIN_REVISION_ID.require()


def test_typed_getters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG_TRACEMALLOC_FRAMES", "40")
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "yes")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", " acme, ,widgets ,")

    assert ENV.DEBUG_TRACEMALLOC_FRAMES.get_int(25) == 40
    assert ENV.LANGSMITH_GATEWAY_ENABLED.get_bool() is True
    assert ENV.ALLOWED_GITHUB_ORGS.get_list() == ["acme", "widgets"]

    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "off")
    assert ENV.LANGSMITH_GATEWAY_ENABLED.get_bool(default=True) is False
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "maybe")
    assert ENV.LANGSMITH_GATEWAY_ENABLED.get_bool(default=True) is True
    monkeypatch.setenv("DEBUG_TRACEMALLOC_FRAMES", "lots")
    with pytest.raises(ValueError, match="DEBUG_TRACEMALLOC_FRAMES"):
        ENV.DEBUG_TRACEMALLOC_FRAMES.get_int(25)


def test_deprecated_in_use_lists_aliases_and_obsolete_names() -> None:
    env = {"LANGSMITH_API_KEY_PROD": "k", "LANGCHAIN_PROJECT": "p", "LANGSMITH_ENDPOINT": "e"}

    found = dict(ENV.deprecated_in_use(env))

    assert found["LANGSMITH_API_KEY_PROD"] == "use LANGSMITH_API_KEY instead."
    assert "LANGCHAIN_PROJECT" in found
    assert "LANGSMITH_ENDPOINT" not in found


def test_deprecated_in_use_is_quiet_for_current_names() -> None:
    assert ENV.deprecated_in_use({"LANGSMITH_API_KEY": "k", "SLACK_BOT_TOKEN": "t"}) == []


def test_undeclared_variables_are_errors() -> None:
    with pytest.raises(AttributeError):
        _ = ENV.NOT_A_DECLARED_VARIABLE
    with pytest.raises(KeyError):
        _ = ENV["NOT_A_DECLARED_VARIABLE"]
    assert "SANDBOX_TYPE" in ENV


def test_registry_rejects_duplicate_declarations() -> None:
    registry = Registry()
    registry.var("X", "first")
    with pytest.raises(ValueError):
        registry.var("X", "again")


def test_secrets_are_flagged() -> None:
    assert ENV.SLACK_BOT_TOKEN.secret
    assert ENV.GITHUB_APP_PRIVATE_KEY.secret
    assert not ENV.SANDBOX_TYPE.secret


def test_no_configuration_is_read_outside_the_registry() -> None:
    """Every literal-named environment read in agent/ goes through ENV."""
    pattern = re.compile(r'os\.(?:environ\.get|getenv)\(\s*"[A-Z]|os\.environ\["[A-Z]')
    agent_root = Path(__file__).resolve().parents[2] / "agent"
    offenders = [
        f"{path.relative_to(agent_root)}:{lineno}"
        for path in agent_root.rglob("*.py")
        if path.name != "config.py"
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []
