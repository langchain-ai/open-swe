"""Each provider's actor, as an endpoint is sent it."""

from __future__ import annotations

from typing import assert_type

from managed_deepagents._channels.actor import (
    GitHubActor,
    GitHubEndpointActor,
    LinearActor,
    LinearEndpointActor,
    SlackActor,
    SlackEndpointActor,
    endpoint_actor,
)


def test_sends_each_providers_actor_in_its_own_shape() -> None:
    slack = endpoint_actor(SlackActor(id="U1", team_id="T1"))
    github = endpoint_actor(GitHubActor(id="1", name="octocat", type="User"))
    linear = endpoint_actor(LinearActor(id="L1", type="user", email="ada@example.com"))

    assert_type(slack, SlackEndpointActor)
    assert_type(github, GitHubEndpointActor)
    assert_type(linear, LinearEndpointActor)
    assert slack == {"provider": "slack", "id": "U1", "teamId": "T1"}
    assert github == {"provider": "github", "id": "1", "name": "octocat", "type": "User"}
    assert linear == {
        "provider": "linear",
        "id": "L1",
        "type": "user",
        "email": "ada@example.com",
    }
