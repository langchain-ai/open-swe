"""The environment registry: precedence, aliases, typed getters, and the no-bypass rule."""

import pytest

from openswe.config import ENV


def test_current_name_wins_over_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "standard")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "legacy")

    assert ENV.LANGSMITH_PROJECT.get() == "standard"
    assert ENV.LANGSMITH_PROJECT.source() == "LANGSMITH_PROJECT"


def test_require_raises_for_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)

    with pytest.raises(KeyError):
        ENV.LANGCHAIN_REVISION_ID.require()


def test_typed_getters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG_TRACEMALLOC_FRAMES", "40")
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "yes")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", " acme, ,widgets ,")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", " alice, ,bob ,")

    assert ENV.DEBUG_TRACEMALLOC_FRAMES.get_int(25) == 40
    assert ENV.LANGSMITH_GATEWAY_ENABLED.get_bool() is True
    assert ENV.ALLOWED_GITHUB_ORGS.get_list() == ["acme", "widgets"]
    assert ENV.ALLOWED_GITHUB_USERS.get_list() == ["alice", "bob"]

    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "off")
    assert ENV.LANGSMITH_GATEWAY_ENABLED.get_bool(default=True) is False
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "maybe")
    assert ENV.LANGSMITH_GATEWAY_ENABLED.get_bool(default=True) is True
    monkeypatch.setenv("DEBUG_TRACEMALLOC_FRAMES", "lots")
    with pytest.raises(ValueError, match="DEBUG_TRACEMALLOC_FRAMES"):
        ENV.DEBUG_TRACEMALLOC_FRAMES.get_int(25)


def test_deprecated_in_use_lists_aliases_and_obsolete_names() -> None:
    env = {"LANGCHAIN_PROJECT": "k", "LANGSMITH_ENDPOINT": "e"}

    found = dict(ENV.deprecated_in_use(env))

    assert found["LANGCHAIN_PROJECT"] == "use LANGSMITH_PROJECT instead."
    assert "LANGSMITH_ENDPOINT" not in found
    # A legacy name on its own, with no current key configured at all, is reported.
    assert "LANGCHAIN_API_KEY" in dict(ENV.deprecated_in_use({"LANGCHAIN_API_KEY": "k"}))


def test_undeclared_variables_are_errors() -> None:
    with pytest.raises(AttributeError):
        _ = ENV.NOT_A_DECLARED_VARIABLE
    with pytest.raises(KeyError):
        _ = ENV["NOT_A_DECLARED_VARIABLE"]
    assert "SANDBOX_TYPE" in ENV
