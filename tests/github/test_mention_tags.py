"""Mention-handle matching, which keeps parallel deployments from double-firing."""

import pytest

from openswe.github import comments as github_comments


@pytest.mark.parametrize(
    "body",
    [
        "@jarvis-aeteq please fix this",
        "hey @jarvis-aeteq, take a look",
        "@Jarvis-Aeteq ping",
        "@jarvis-aeteq: do the thing",
        "(@jarvis-aeteq)",
    ],
)
def test_matches_configured_handles(body: str) -> None:
    assert github_comments.mentions_open_swe(body)


@pytest.mark.parametrize(
    "body",
    [
        "@jarvis-aeteq-preview please fix this",
        "@jarvis-aeteqfoo",
        "no mention here",
        "",
        None,
    ],
)
def test_ignores_longer_handles_and_empty(body: str | None) -> None:
    assert not github_comments.mentions_open_swe(body)
