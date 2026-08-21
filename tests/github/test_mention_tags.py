"""Mention-handle matching, which keeps parallel deployments from double-firing."""

import pytest

from agent.utils import github_comments


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


def test_env_overrides_handles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_MENTION_TAGS", "@openswe-preview")
    assert github_comments.open_swe_tags() == ("@openswe-preview",)
    assert github_comments.mentions_open_swe("@openswe-preview ship it")
    assert not github_comments.mentions_open_swe("@openswe ship it")


def test_blank_env_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_MENTION_TAGS", "  ,  ")
    assert github_comments.open_swe_tags() == github_comments._DEFAULT_OPEN_SWE_TAGS
