"""Normalize validated dispatch events into the callback attribute interface."""

from __future__ import annotations

from managed_deepagents._channels.github_mentions import event_mentions
from managed_deepagents._channels.github_schema import (
    GitHubInstallationEventKey,
    SupportedEventKey,
    validate_installation_payload,
    validate_payload,
)
from managed_deepagents._channels.github_types import (
    GitHubAccount,
    GitHubEvent,
    GitHubInstallationEvent,
    GitHubRepository,
    GitHubSender,
)


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


def github_installation_event(
    key: GitHubInstallationEventKey, payload: object, delivery_id: str
) -> GitHubInstallationEvent:
    validated = validate_installation_payload(key, payload)
    installation = validated["installation"]
    account = installation["account"]
    sender = validated["sender"]
    return GitHubInstallationEvent(
        key=key,
        name=key.split(".")[0],
        action=validated["action"],
        delivery_id=delivery_id,
        installation_id=installation["id"],
        account=GitHubAccount(id=account["id"], login=account.get("login")),
        sender=GitHubSender(id=sender["id"], login=sender["login"], type=sender["type"]),
        payload=validated,
    )
