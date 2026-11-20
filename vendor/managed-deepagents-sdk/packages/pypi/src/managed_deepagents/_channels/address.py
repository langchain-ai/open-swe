"""The typed provider conversation a binding's target names."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing_extensions import TypeIs

from managed_deepagents._channels.runtime import json_copy
from managed_deepagents._channels.types import (
    ChannelAddress,
    GitHubChannelAddress,
    LinearChannelAddress,
    SlackChannelAddress,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from managed_deepagents._channels.binding import ChannelBinding

_NONBLANK = r"\S"


class _SlackAddressModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    conversation_id: str = Field(pattern=r"^[CDG][A-Z0-9]+$")
    thread_id: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+$")


class _SlackTargetModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    address: _SlackAddressModel


class _GitHubTargetModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    repository: str = Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    number: int = Field(gt=0, le=2**53 - 1)


class _LinearTargetModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    organizationId: str = Field(pattern=_NONBLANK)  # noqa: N815 - the stored key
    issueId: str = Field(pattern=_NONBLANK)  # noqa: N815 - the stored key
    identifier: str | None = Field(default=None, pattern=_NONBLANK)
    commentId: str | None = Field(default=None, pattern=_NONBLANK)  # noqa: N815 - the stored key
    sessionId: str | None = Field(default=None, pattern=_NONBLANK)  # noqa: N815 - the stored key


def channel_address(
    binding: ChannelBinding, event: Mapping[str, object] | None = None
) -> ChannelAddress | None:
    """The conversation a binding posts to, when its provider has a typed address.

    ``None`` when the binding's target is malformed. Only the conversation's own ids are
    copied; the Trigger channel and the rest of its address stay internal. A
    schedule that starts a new thread posts at top level, so its address has no
    thread.
    """
    provider, transport = binding["provider"], binding["transport"]
    new_thread = (
        event is not None and event.get("type") == "schedule" and event.get("new_thread") is True
    )
    target = json_copy(binding["target"])
    try:
        if provider == "slack" and transport == "trigger_server":
            address = _SlackTargetModel.model_validate(target).address
            return SlackChannelAddress(
                conversation_id=address.conversation_id,
                thread_ts=None if new_thread else address.thread_id,
            )
        if provider == "github" and transport == "direct":
            github = _GitHubTargetModel.model_validate(target)
            return GitHubChannelAddress(repository=github.repository, number=github.number)
        if provider == "linear" and transport == "direct":
            linear = _LinearTargetModel.model_validate(target)
            return LinearChannelAddress(
                organization_id=linear.organizationId,
                issue_id=linear.issueId,
                identifier=linear.identifier,
                comment_id=None if new_thread else linear.commentId,
                session_id=None if new_thread else linear.sessionId,
            )
    except ValidationError:
        return None
    return None


def is_channel_address(value: object) -> TypeIs[ChannelAddress]:
    """Whether ``value`` is a ``ChannelAddress`` of a provider MDA knows."""
    return isinstance(value, SlackChannelAddress | GitHubChannelAddress | LinearChannelAddress)


def names_origin(target: ChannelAddress, origin: ChannelAddress) -> bool:
    """Whether ``target`` addresses the post ``origin`` does.

    Only the fields that address a post count: Slack's conversation and thread,
    a GitHub repository (in any case) and number, and a Linear organization and
    issue, with its comment or session only when ``target`` names one.
    ``channel`` is left out, so a run may pass back the address its thread was
    given.
    """
    if isinstance(target, SlackChannelAddress) and isinstance(origin, SlackChannelAddress):
        return (target.conversation_id, target.thread_ts) == (
            origin.conversation_id,
            origin.thread_ts,
        )
    if isinstance(target, GitHubChannelAddress) and isinstance(origin, GitHubChannelAddress):
        return (target.repository.lower(), target.number) == (
            origin.repository.lower(),
            origin.number,
        )
    if isinstance(target, LinearChannelAddress) and isinstance(origin, LinearChannelAddress):
        return (
            (target.organization_id, target.issue_id) == (origin.organization_id, origin.issue_id)
            and target.comment_id in (None, origin.comment_id)
            and target.session_id in (None, origin.session_id)
        )
    return False
