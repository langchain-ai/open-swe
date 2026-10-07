"""Mention-handle matching, which keeps parallel deployments from double-firing."""

import pytest

from openswe.github import comments as github_comments


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
