"""Channels whose human messages can start and continue threads without a mention."""

from pydantic import BaseModel, ConfigDict, field_validator

from agent.store import TypedStore
from agent.workspaces.store import normalize_slack_channel_id


class UntaggedChannel(BaseModel):
    channel_id: str


class SetUntaggedChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str

    @field_validator("channel_id")
    @classmethod
    def valid_channel_id(cls, value: str) -> str:
        return normalize_slack_channel_id(value)


UNTAGGED_CHANNELS = TypedStore(["slack_untagged_channels"], UntaggedChannel)


async def allows_untagged_messages(channel_id: str) -> bool:
    return await UNTAGGED_CHANNELS.get(channel_id) is not None
