import logging
import posixpath
import re
from collections.abc import Sequence
from typing import Annotated
from urllib.parse import urlsplit

import httpx2
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, ValidationError
from runtime_api.models import gateway_overrides
from runtime_api.slack import SlackMessageOrigin

from openswe.run_config import RunConfig
from openswe.slack.client import (
    SlackFileDownloadError,
    download_slack_file,
    fetch_slack_message_by_ts,
    fetch_slack_thread_message_by_ts,
)
from openswe.slack.dm import CONCIERGE_TS
from openswe.slack.payloads import SlackMessage
from openswe.source_context import SlackThreadRef
from openswe.tools.access import direct_user_run
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 24_000_000


class SlackVoiceClip(BaseModel):
    origin: SlackMessageOrigin
    id: str
    user: str
    subtype: str = ""
    mimetype: str = ""
    name: str
    size: int = Field(gt=0, le=MAX_AUDIO_BYTES)
    url_private: str
    is_external: bool = False
    deleted: bool = False

    @staticmethod
    async def direct_thread(config: RunConfig) -> SlackThreadRef:
        """The Slack conversation of a run a person started directly from Slack."""
        slack = config.slack_thread
        if (
            config.source != "slack"
            or not direct_user_run(config)
            or config.mcp_caller
            or not config.thread_id
            or slack is None
            or slack.triggering_bot_id
            or not all(
                (
                    slack.channel_id,
                    slack.thread_ts,
                    slack.triggering_event_ts,
                    slack.triggering_user_id,
                )
            )
        ):
            raise ValueError("Transcription requires a direct Slack voice message.")
        return slack

    @staticmethod
    async def source(
        slack: SlackThreadRef, messages: Sequence[BaseMessage], message_ts: str | None
    ) -> SlackMessageOrigin:
        """The verified Slack origin of the person's request ``message_ts`` names, or their latest."""
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            origin = SlackMessageOrigin.of(message.additional_kwargs)
            if origin is None or (message_ts is not None and origin.message_ts != message_ts):
                continue
            if origin.channel_id != slack.channel_id or origin.thread_ts != slack.thread_ts:
                continue
            return origin
        if message_ts is None:
            raise ValueError(
                "No message in this conversation records a verified Slack origin, "
                "so there is no voice message to transcribe."
            )
        raise ValueError(
            f"Slack message {message_ts} is not a direct request this conversation received "
            "with a verified Slack origin, so its audio can't be transcribed."
        )

    @staticmethod
    async def delivered(
        origin: SlackMessageOrigin, slack: SlackThreadRef
    ) -> dict[str, object] | None:
        """The Slack message ``origin`` names, as Slack returns it now."""
        if origin.thread_ts == CONCIERGE_TS and origin.reply_thread_ts:
            return await fetch_slack_thread_message_by_ts(
                origin.channel_id, origin.reply_thread_ts, origin.message_ts
            )
        if origin.thread_ts not in {origin.message_ts, CONCIERGE_TS}:
            return await fetch_slack_thread_message_by_ts(
                origin.channel_id, origin.thread_ts, origin.message_ts
            )
        found = await fetch_slack_message_by_ts(origin.channel_id, origin.message_ts)
        # A concierge DM's conversation spans its Slack threads; the run names the trigger's.
        if found is None and origin.thread_ts == CONCIERGE_TS and slack.reply_thread_ts:
            found = await fetch_slack_thread_message_by_ts(
                origin.channel_id, slack.reply_thread_ts, origin.message_ts
            )
        return found

    @classmethod
    async def resolve(
        cls,
        config: RunConfig,
        file_id: str,
        messages: Sequence[BaseMessage],
        message_ts: str | None,
    ) -> SlackVoiceClip:
        slack = await cls.direct_thread(config)
        origin = await cls.source(slack, messages, message_ts)
        if not re.fullmatch(r"F[A-Z0-9]+", file_id):
            raise ValueError("Use a Slack file ID, not a path or URL.")
        payload = await cls.delivered(origin, slack)
        if payload is None:
            raise ValueError(
                f"Slack did not return message {origin.message_ts}, so its sender "
                "and voice clip could not be verified."
            )
        message = SlackMessage.model_validate(payload)
        if message.ts != origin.message_ts or message.user != origin.user_id or message.is_from_bot:
            raise ValueError("The delivered Slack sender could not be verified.")
        files = payload.get("files")
        raw = (
            next(
                (item for item in files if isinstance(item, dict) and item.get("id") == file_id),
                None,
            )
            if isinstance(files, list)
            else None
        )
        if raw is None:
            raise ValueError("The file is not attached to the selected direct Slack message.")
        clip = cls.model_validate({**raw, "origin": origin})
        if (
            clip.subtype != "slack_audio"
            or clip.user != message.user
            or clip.is_external
            or clip.deleted
            or not clip.mimetype.startswith("audio/")
            or posixpath.splitext(clip.name)[1].lower()
            not in {".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".wav", ".webm"}
        ):
            raise ValueError("Only the sender's native Slack voice clips can be transcribed.")
        url = urlsplit(clip.url_private)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or not (host == "files.slack.com" or host.endswith(".files.slack.com"))
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
        ):
            raise ValueError("The voice clip must be hosted by Slack.")
        return clip


class SpeechTranscript(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


class SpeechProvider(BaseModel):
    base_url: str
    api_key: str = Field(repr=False)

    @classmethod
    def configured(cls) -> SpeechProvider:
        return cls.model_validate(gateway_overrides("openai:gpt-transcribe"))

    async def transcribe(self, name: str, content: bytes) -> str:
        async with httpx2.AsyncClient(transport=pooled_transport(), timeout=120) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={"model": "gpt-transcribe", "response_format": "json"},
                files={"file": (name, content, "application/octet-stream")},
                follow_redirects=False,
            )
            response.raise_for_status()
            transcript = SpeechTranscript.model_validate_json(response.content)
            text = transcript.text.strip()
            if not text:
                raise ValueError("No speech was recognized in the audio.")
            return text


async def transcribe_audio(
    file_id: str,
    message_ts: str | None = None,
    messages: Annotated[list[BaseMessage] | None, InjectedState("messages")] = None,
) -> dict[str, object]:
    """Transcribe a verified voice clip on a delivered direct Slack message."""
    config = RunConfig.from_runtime()
    try:
        clip = await SlackVoiceClip.resolve(config, file_id, messages or [], message_ts)
        provider = SpeechProvider.configured()
        content = await download_slack_file(clip.url_private)
        if not 0 < len(content) <= MAX_AUDIO_BYTES:
            raise ValueError("Audio must be nonempty and no larger than 24 MB.")
        text = await provider.transcribe(posixpath.basename(clip.name), content)
        return {
            "file_id": clip.id,
            "text": text,
            "source": "verified Slack voice message",
            "sender_id": f"slack:{clip.user}",
            "channel_id": clip.origin.channel_id,
            "thread_ts": clip.origin.thread_ts,
            "message_ts": clip.origin.message_ts,
        }
    except (ValueError, SlackFileDownloadError) as error:
        logger.warning("Audio transcription rejected", extra={"error_type": type(error).__name__})
        if isinstance(error, ValidationError):
            return {"error": "Voice message metadata or transcription was invalid."}
        return {"error": str(error)}
    except httpx2.HTTPError as error:
        logger.warning(
            "Audio transcription provider failed", extra={"error_type": type(error).__name__}
        )
        return {
            "error": "Audio transcription failed at the configured provider. No transcript is available."
        }
