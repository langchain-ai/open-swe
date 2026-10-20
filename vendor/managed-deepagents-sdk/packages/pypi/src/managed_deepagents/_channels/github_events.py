"""Normalize validated dispatch events into the callback attribute interface."""

from __future__ import annotations

from managed_deepagents._channels.github_mentions import event_mentions
from managed_deepagents._channels.github_schema import SupportedEventKey, validate_payload
from managed_deepagents._channels.github_types import GitHubEvent, GitHubRepository, GitHubSender


def github_event(key: SupportedEventKey, payload: object, delivery_id: str) -> GitHubEvent:
    validated = validate_payload(key, payload)
    repository = validated["repository"]
    sender = validated["sender"]
    name = key.split(".")[0]
    return GitHubEvent(
        key=key,
        name=name,
        action=validated.get("action"),
        delivery_id=delivery_id,
        installation_id=validated["installation"]["id"],
        repository=GitHubRepository(
            id=repository["id"],
            owner=repository["owner"]["login"],
            name=repository["name"],
            full_name=repository["full_name"],
            private=repository["private"],
        ),
        sender=GitHubSender(id=sender["id"], login=sender["login"], type=sender["type"]),
        payload=validated,
        mentions=event_mentions(key, validated),
    )
