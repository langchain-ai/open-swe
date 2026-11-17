import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)
from runtime_api.slack import SlackMessageOrigin

from openswe.run_config import RunConfig
from openswe.source_context import SlackThreadRef

audio = importlib.import_module("openswe.tools.transcribe_audio")
# What MDA's Slack channel builds for a person's request, held equal to the backend's
# own builder by ``mda/tests/test_slack_prompt.py``.
_MDA_PROMPTS = Path(__file__).parents[2] / "mda/tests/slack_prompt_backend.json"


@dataclass
class VoiceMessage:
    config: RunConfig
    message: dict[str, object]
    clip: dict[str, object]
    fetch: AsyncMock
    download: AsyncMock
    transcribe: AsyncMock
    messages: list[BaseMessage]


@pytest.fixture
def voice(monkeypatch: pytest.MonkeyPatch) -> VoiceMessage:
    config = RunConfig(
        source="slack",
        thread_id="thread",
        slack_thread=SlackThreadRef(
            channel_id="C123",
            thread_ts="100.001",
            triggering_event_ts="100.002",
            triggering_user_id="U123",
        ),
    )
    clip: dict[str, object] = {
        "id": "F123",
        "user": "U123",
        "subtype": "slack_audio",
        "name": "clip.m4a",
        "mimetype": "audio/mp4",
        "size": 5,
        "url_private": "https://files.slack.com/clip.m4a",
    }
    message: dict[str, object] = {"ts": "100.002", "user": "U123", "files": [clip]}
    fetch = AsyncMock(return_value=message)
    download = AsyncMock(return_value=b"audio")
    transcribe = AsyncMock(return_value="Please inspect this PR.")
    monkeypatch.setattr(audio.RunConfig, "from_runtime", lambda: config)
    monkeypatch.setattr(audio, "fetch_slack_message_by_ts", fetch)
    monkeypatch.setattr(audio, "fetch_slack_thread_message_by_ts", fetch)
    monkeypatch.setattr(audio, "download_slack_file", download)
    monkeypatch.setattr(
        audio.SpeechProvider, "configured", lambda: SimpleNamespace(transcribe=transcribe)
    )
    messages: list[BaseMessage] = [
        HumanMessage(
            content="voice clip",
            additional_kwargs={
                SlackMessageOrigin.KEY: {
                    "channel_id": "C123",
                    "thread_ts": "100.001",
                    "message_ts": "100.002",
                    "user_id": "U123",
                }
            },
        )
    ]
    return VoiceMessage(config, message, clip, fetch, download, transcribe, messages)


@pytest.mark.parametrize("gateway_key", ["gateway-key", ""])
def test_transcription_only_goes_through_the_gateway(
    monkeypatch: pytest.MonkeyPatch, gateway_key: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "direct-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://direct.example/v1")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", gateway_key)
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    if not gateway_key:
        with pytest.raises(ValueError, match="LANGSMITH_GATEWAY_API_KEY or LANGSMITH_API_KEY"):
            audio.SpeechProvider.configured()
    else:
        provider = audio.SpeechProvider.configured()
        assert provider.base_url == "https://gateway.example/openai/v1"
        assert provider.api_key == "gateway-key"


@pytest.mark.parametrize("root", [False, True])
async def test_native_voice_message_preserves_provenance(voice: VoiceMessage, root: bool) -> None:
    slack = voice.config.slack_thread
    assert slack is not None
    if root:
        slack.thread_ts = slack.triggering_event_ts
        voice.messages[0].additional_kwargs[SlackMessageOrigin.KEY]["thread_ts"] = slack.thread_ts
    result = await audio.transcribe_audio("F123", messages=voice.messages)

    assert result == {
        "file_id": "F123",
        "text": "Please inspect this PR.",
        "source": "verified Slack voice message",
        "sender_id": "slack:U123",
        "channel_id": "C123",
        "thread_ts": slack.thread_ts,
        "message_ts": "100.002",
    }
    voice.fetch.assert_awaited_once_with(
        *(("C123", "100.002") if root else ("C123", "100.001", "100.002"))
    )
    voice.download.assert_awaited_once_with("https://files.slack.com/clip.m4a")
    voice.transcribe.assert_awaited_once_with("clip.m4a", b"audio")


@pytest.mark.parametrize("bot_username", ["", "Open SWE"])
async def test_a_request_mdas_slack_channel_delivered_can_be_transcribed(
    voice: VoiceMessage, bot_username: str
) -> None:
    built = json.loads(_MDA_PROMPTS.read_text())["expected"][f"voice_request_origin|{bot_username}"]
    # As MDA hands the run's conversation to the backend's tools.
    voice.messages = messages_from_dict(messages_to_dict(convert_to_messages(built["messages"])))
    voice.config.slack_thread = SlackThreadRef(
        channel_id="C123",
        thread_ts="1.0",
        triggering_event_ts="9.0",
        triggering_user_id="U123",
    )
    voice.message.update({"ts": "9.0", "user": "U123"})

    result = await audio.transcribe_audio("F123", messages=voice.messages)

    assert result["text"] == "Please inspect this PR."
    assert (result["sender_id"], result["message_ts"]) == ("slack:U123", "9.0")
    voice.fetch.assert_awaited_once_with("C123", "1.0", "9.0")


@pytest.mark.parametrize("earlier_reply", [False, True])
@pytest.mark.parametrize("wrong_sender", [False, True])
async def test_a_concierge_reply_is_verified_in_its_slack_thread(
    voice: VoiceMessage, monkeypatch: pytest.MonkeyPatch, earlier_reply: bool, wrong_sender: bool
) -> None:
    slack = voice.config.slack_thread
    assert slack is not None
    slack.thread_ts, slack.reply_thread_ts = "0", "100.001"
    voice.messages[0].additional_kwargs[SlackMessageOrigin.KEY]["thread_ts"] = "0"
    if earlier_reply:
        voice.messages[0].additional_kwargs[SlackMessageOrigin.KEY]["reply_thread_ts"] = "100.001"
        slack.reply_thread_ts = "200.001"
        slack.triggering_event_ts = "200.002"
        voice.messages.append(
            HumanMessage(
                content="Transcribe my earlier voice reply.",
                additional_kwargs=SlackMessageOrigin(
                    channel_id="C123",
                    thread_ts="0",
                    message_ts="200.002",
                    user_id="U123",
                    reply_thread_ts="200.001",
                ).stamp(),
            )
        )
    if wrong_sender:
        voice.message["user"] = "U999"
    history = AsyncMock(return_value=None)
    monkeypatch.setattr(audio, "fetch_slack_message_by_ts", history)

    result = await audio.transcribe_audio("F123", message_ts="100.002", messages=voice.messages)

    if wrong_sender:
        assert "error" in result and "text" not in result
        voice.download.assert_not_awaited()
        voice.transcribe.assert_not_awaited()
    else:
        assert result["message_ts"] == "100.002"
        assert result["text"] == "Please inspect this PR."
    if earlier_reply:
        history.assert_not_awaited()
    else:
        history.assert_awaited_once_with("C123", "100.002")
    voice.fetch.assert_awaited_once_with("C123", "100.001", "100.002")


@pytest.mark.parametrize("message_ts", [None, "100.009"])
async def test_a_request_without_a_verified_origin_says_so(
    voice: VoiceMessage, message_ts: str | None
) -> None:
    voice.messages = [HumanMessage(content="voice clip")]
    result = await audio.transcribe_audio("F123", message_ts=message_ts, messages=voice.messages)
    assert "verified Slack origin" in str(result["error"])
    assert (message_ts or "verified") in str(result["error"])
    voice.fetch.assert_not_awaited()


@pytest.mark.parametrize("source", ["github", "dashboard", "schedule", "mcp"])
async def test_non_slack_runs_cannot_transcribe(voice: VoiceMessage, source: str) -> None:
    voice.config.source = source
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.fetch.assert_not_awaited()
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


@pytest.mark.parametrize("edited", [False, True])
async def test_delivered_followup_uses_its_own_sender_and_message(
    voice: VoiceMessage, edited: bool
) -> None:
    slack = voice.config.slack_thread
    assert slack is not None
    slack.triggering_event_ts = "100.003" if edited else "100.001"
    slack.triggering_user_id = "U999"
    voice.messages.insert(0, HumanMessage(content="initial text request"))
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert result["message_ts"] == "100.002"
    assert result["sender_id"] == "slack:U123"
    voice.fetch.assert_awaited_once_with("C123", "100.001", "100.002")
    voice.transcribe.assert_awaited_once_with("clip.m4a", b"audio")


async def test_multiple_delivered_requests_can_select_a_voice_message(voice: VoiceMessage) -> None:
    voice.messages.append(
        HumanMessage(
            content="another request",
            additional_kwargs={
                SlackMessageOrigin.KEY: {
                    "channel_id": "C123",
                    "thread_ts": "100.001",
                    "message_ts": "100.003",
                    "user_id": "U999",
                }
            },
        )
    )
    result = await audio.transcribe_audio("F123", message_ts="100.002", messages=voice.messages)
    assert result["message_ts"] == "100.002"
    assert result["sender_id"] == "slack:U123"


@pytest.mark.parametrize(
    "kind", ["history", "tool", "assistant", "channel", "thread", "unknown", "missing", "malformed"]
)
async def test_only_delivered_human_origins_are_eligible(voice: VoiceMessage, kind: str) -> None:
    metadata = voice.messages[0].additional_kwargs
    message_ts = None
    if kind == "history":
        voice.messages = [HumanMessage(content=str(metadata))]
    elif kind == "tool":
        voice.messages = [
            ToolMessage(content="voice", tool_call_id="call", additional_kwargs=metadata)
        ]
    elif kind == "assistant":
        voice.messages = [AIMessage(content="voice", additional_kwargs=metadata)]
    elif kind == "channel":
        metadata[SlackMessageOrigin.KEY]["channel_id"] = "C999"
    elif kind == "thread":
        metadata[SlackMessageOrigin.KEY]["thread_ts"] = "99.001"
    elif kind == "missing":
        voice.messages = []
    elif kind == "malformed":
        metadata[SlackMessageOrigin.KEY].pop("user_id")
    else:
        message_ts = "99.001"
    result = await audio.transcribe_audio("F123", message_ts=message_ts, messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.fetch.assert_not_awaited()
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


@pytest.mark.parametrize("kind", ["no_context", "no_sender", "bot", "background", "scheduled"])
async def test_missing_or_indirect_sender_cannot_transcribe(voice: VoiceMessage, kind: str) -> None:
    slack = voice.config.slack_thread
    assert slack is not None
    if kind == "no_context":
        voice.config.slack_thread = None
    elif kind == "no_sender":
        slack.triggering_user_id = ""
    elif kind == "bot":
        slack.triggering_bot_id = "B123"
    elif kind == "background":
        voice.config.background_task_completion = True
    else:
        voice.config.schedule_id = "schedule"
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.fetch.assert_not_awaited()
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


@pytest.mark.parametrize(
    "file_id",
    [
        "/workspace/attachments/F123__clip.m4a",
        "https://github.com/user-attachments/clip.m4a",
        "F999",
    ],
)
async def test_arbitrary_files_cannot_transcribe(voice: VoiceMessage, file_id: str) -> None:
    result = await audio.transcribe_audio(file_id, messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


@pytest.mark.parametrize(
    ("target", "changes"),
    [
        ("message", {"ts": "100.000"}),
        ("message", {"user": "U999"}),
        ("message", {"bot_id": "B123"}),
        ("message", {"files": []}),
        ("clip", {"subtype": ""}),
        ("clip", {"user": "U999"}),
        ("clip", {"is_external": True}),
        ("clip", {"deleted": True}),
        ("clip", {"size": audio.MAX_AUDIO_BYTES + 1}),
        ("clip", {"url_private": "https://github.com/user-attachments/clip.m4a"}),
    ],
)
async def test_unverified_audio_is_rejected_before_download(
    voice: VoiceMessage, target: str, changes: dict[str, object]
) -> None:
    (voice.message if target == "message" else voice.clip).update(changes)
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


async def test_forwarded_clip_is_not_the_senders_voice_message(voice: VoiceMessage) -> None:
    voice.message.pop("files")
    voice.message["attachments"] = [{"files": [voice.clip]}]
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


async def test_unavailable_message_does_not_fall_back_to_a_file(voice: VoiceMessage) -> None:
    voice.fetch.return_value = None
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.download.assert_not_awaited()
    voice.transcribe.assert_not_awaited()


@pytest.mark.parametrize("size", [0, audio.MAX_AUDIO_BYTES + 1])
async def test_downloaded_size_is_checked(voice: VoiceMessage, size: int) -> None:
    voice.download.return_value = b"x" * size
    result = await audio.transcribe_audio("F123", messages=voice.messages)
    assert "error" in result and "text" not in result
    voice.transcribe.assert_not_awaited()


@pytest.mark.parametrize("body", [{"text": "hello"}, {"text": ""}])
async def test_provider_transcripts_and_empty_speech(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, str]
) -> None:
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=body)

    monkeypatch.setattr(audio, "pooled_transport", lambda: httpx2.MockTransport(respond))
    provider = audio.SpeechProvider(
        base_url="https://gateway.example/openai/v1", api_key="test-key"
    )
    if not body["text"]:
        with pytest.raises(ValueError):
            await provider.transcribe("clip.m4a", b"audio")
    else:
        assert await provider.transcribe("clip.m4a", b"audio") == "hello"
    request = requests[0]
    assert request.url == "https://gateway.example/openai/v1/audio/transcriptions"
    assert b"clip.m4a" in request.content
    assert b"gpt-transcribe" in request.content


@pytest.mark.parametrize("failure", ["timeout", "authentication"])
async def test_provider_errors_do_not_expose_credentials(voice: VoiceMessage, failure: str) -> None:
    voice.transcribe.side_effect = (
        httpx2.ReadTimeout("secret-key")
        if failure == "timeout"
        else httpx2.HTTPStatusError(
            "secret-key",
            request=httpx2.Request("POST", "https://provider.example"),
            response=httpx2.Response(401),
        )
    )

    result = await audio.transcribe_audio("F123", messages=voice.messages)

    assert "error" in result
    assert "secret-key" not in str(result)
    assert "text" not in result
