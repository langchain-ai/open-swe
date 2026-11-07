"""Mention-handle matching, which keeps parallel deployments from double-firing."""

import pytest

from openswe.github import comments as github_comments


@pytest.mark.parametrize(
    "body",
    [
        "@robin-agent please fix this",
        "@Robin-Agent: please fix this",
        "(@robin-agent)",
        "hey @robin-agent, take a look",
        "please ask\n@robin-agent to help",
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
        "@robin please fix this",
        "@Robin: please fix this",
        "@robin-preview please fix this",
        "@robin-agent-preview please fix this",
        "@robin-agentfoo",
        "me@robin.io",
        "me@robin-agent.io",
        "me@openswe.io",
        "user.@robin-agent",
        "user-@robin-agent",
        "@@robin-agent",
        "@openswe-preview please fix this",
        "@openswefoo",
        "no mention here",
        "",
        None,
    ],
)
def test_ignores_longer_handles_and_empty(body: str | None) -> None:
    assert not github_comments.mentions_open_swe(body)
