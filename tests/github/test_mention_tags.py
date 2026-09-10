"""Mention-handle matching, which keeps parallel deployments from double-firing."""

import importlib

import pytest

from agent.github import comments as github_comments


@pytest.fixture
def reload_tags(monkeypatch):
    def _reload(value: str | None):
        if value is None:
            monkeypatch.delenv("OPEN_SWE_MENTION_TAGS", raising=False)
        else:
            monkeypatch.setenv("OPEN_SWE_MENTION_TAGS", value)
        return importlib.reload(github_comments)

    yield _reload
    monkeypatch.delenv("OPEN_SWE_MENTION_TAGS", raising=False)
    importlib.reload(github_comments)


@pytest.mark.parametrize(
    "body",
    [
        "@openswe please fix this",
        "hey @open-swe, take a look",
        "@OpenSWE ping",
        "cc @openswe-dev",
        "@openswe: do the thing",
        "(@openswe)",
    ],
)
def test_matches_configured_handles(body: str) -> None:
    assert github_comments.mentions_open_swe(body)


@pytest.mark.parametrize(
    "body",
    [
        "@openswe-preview please fix this",
        "@openswefoo",
        "no mention here",
        "",
        None,
    ],
)
def test_ignores_longer_handles_and_empty(body: str | None) -> None:
    assert not github_comments.mentions_open_swe(body)


def test_env_overrides_handles(reload_tags) -> None:
    module = reload_tags("@openswe-preview")
    assert module.OPEN_SWE_TAGS == ("@openswe-preview",)
    assert module.mentions_open_swe("@openswe-preview ship it")
    assert not module.mentions_open_swe("@openswe ship it")


def test_blank_env_falls_back_to_defaults(reload_tags) -> None:
    module = reload_tags("  ,  ")
    assert module.OPEN_SWE_TAGS == module._DEFAULT_OPEN_SWE_TAGS
