"""Attachment authority, binary transfers, and native graph integration."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from managed_deepagents import runtime as runtime_module
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._channels.slack import files
from managed_deepagents._channels.slack.middleware import (
    SlackFileTransferMiddleware,
    prepare_request,
)
from managed_deepagents._connections import connections
from managed_deepagents._managed_tools import managed_runtime_overrides
from managed_deepagents.define_deep_agent import DeepAgentDefinition

SOURCE = "opaque-source-key"
DESTINATION = {"conversation_id": "C1", "thread_id": "123.456"}
BINARY = b"\x00\xff"
ATTACHMENT = {"id": "F1", "name": "../data.bin", "url_private": "https://files.slack.com/a"}


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    register_channel_messaging([SimpleNamespace(name="slack", kind="slack")])
    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    user = {"mda_user_id": "user-1", "mda_source_provider": "slack", "mda_source_thread_id": SOURCE}
    channel = {
        "name": "slack",
        "provider": "slack",
        "source_thread_key": SOURCE,
        "delivery_id": "delivery-1",
        "transport": "trigger_server",
        "target": {
            "channel_id": "managed-channel",
            "source_thread_key": SOURCE,
            "address": dict(DESTINATION),
        },
        "event": {"type": "message", "messages": []},
        "raw_event": {"files": [ATTACHMENT]},
    }

    async def token(slug: str, owner: object) -> str:
        return "secret"

    monkeypatch.setattr(connections, "get", token)
    return SimpleNamespace(
        backend=backend,
        context={"mda_channel": channel},
        server_info=SimpleNamespace(user=user),
    )


def history_response(request: httpx.Request, state: SimpleNamespace) -> httpx.Response:
    assert request.method == "GET"
    assert request.content == b""
    assert request.headers["authorization"] == "Bearer secret"
    state.history_calls.append(dict(request.url.params))
    page = state.history_pages[min(len(state.history_calls) - 1, len(state.history_pages) - 1)]
    if isinstance(page, Exception):
        raise page
    return httpx.Response(200, json=page)


def upload_url_response(request: httpx.Request, state: SimpleNamespace) -> httpx.Response:
    if state.failure == "api_network":
        raise httpx.ReadError("secret")
    assert request.method == "GET"
    assert request.content == b""
    assert dict(request.url.params) == {
        "filename": "result.bin",
        "length": str(len(BINARY)),
    }
    return httpx.Response(
        302 if state.failure == "api_redirect" else 200,
        json={
            "ok": state.failure != "missing_scope",
            "error": "missing_scope",
            "upload_url": "https://files.slack.com/upload",
            "file_id": "F1",
        },
        headers={"location": "https://evil.test"},
    )


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        calls=[], history_calls=[], history_pages=[{"ok": True, "messages": []}], failure=None
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.replies"):
            return history_response(request, state)
        state.calls.append(request)
        if request.method == "GET" and request.url.host == "files.slack.com":
            status = {"download_denied": 403, "download_redirect": 302}.get(state.failure, 200)
            return httpx.Response(
                status,
                stream=httpx.ByteStream(BINARY),
                headers={"location": "https://evil.test"} if status == 302 else {},
            )
        if request.url.host == "slack.com":
            assert request.headers["authorization"] == "Bearer secret"
            if request.method == "POST":
                assert request.headers["content-type"].startswith("application/json")
        if request.method == "GET" and request.url.host == "slack.com":
            return upload_url_response(request, state)
        if request.url.path == "/upload":
            assert "authorization" not in request.headers
            assert request.content == BINARY
            return httpx.Response(200)
        assert json.loads(request.content)["thread_ts"] == DESTINATION["thread_id"]
        assert "initial_comment" not in json.loads(request.content)
        if state.failure == "network":
            raise httpx.ReadError("secret")
        if state.failure == "malformed":
            return httpx.Response(200, content=b"secret")
        return httpx.Response(
            302 if state.failure == "redirect" else 200,
            json={"ok": state.failure is None, "error": "secret"},
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        files.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return state


def test_destination_uses_target_without_decoding_source_or_reading_raw_event(
    runtime: SimpleNamespace,
) -> None:
    runtime.context["mda_channel"].pop("raw_event")
    assert files.get_slack_destination(runtime) == DESTINATION


@pytest.mark.parametrize(
    ("channel", "timestamp"),
    [
        ("", "123.456"),
        ("bad-channel", "123.456"),
        ("C1", ""),
        ("C1", "123"),
        ("C1", "123.456/other"),
        ("C1\n", "123.456"),
        ("C1", "123.456\n"),
        ("C1", None),
    ],
)
def test_malformed_destination(
    runtime: SimpleNamespace, channel: str, timestamp: str | None
) -> None:
    runtime.context["mda_channel"]["target"]["address"] = {
        "conversation_id": channel,
        "thread_id": timestamp,
    }
    assert files.get_slack_destination(runtime) is None


def test_forged_configurable_source_cannot_authorize_files() -> None:
    raw = SimpleNamespace(
        config={
            "configurable": {
                "langgraph_auth_user": {"mda_user_id": "api-user"},
                "mda_source_provider": "slack",
                "thread_id": SOURCE,
            }
        }
    )
    register_channel_messaging([SimpleNamespace(name="slack", kind="slack")])
    managed = SimpleNamespace(context=None, **managed_runtime_overrides(raw))
    assert managed.server_info.source.provider == "slack"
    assert files.get_slack_destination(managed) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://files.slack.com/a",
        "https://slack.com.evil.test/a",
        "https://files.slack.com\\@evil.test/a",
    ],
)
def test_unsafe_urls(url: str) -> None:
    with pytest.raises(files.SlackError):
        files.slack_url(url)


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../../etc/passwd", "/workspace/..", "a\\b", "a\x00b"]
)
def test_workspace_paths(path: str) -> None:
    with pytest.raises(files.SlackError):
        files.workspace_path(path)


@pytest.mark.parametrize(
    "change",
    [
        {"is_external": True},
        {"deleted": True},
        {"size": files.MAX_FILE_BYTES + 1},
        {"id": "../bad"},
        {"url_private": ""},
    ],
)
def test_invalid_attachment_metadata(change: dict[str, object]) -> None:
    with pytest.raises(files.SlackError):
        files.resolve_incoming_attachment({**ATTACHMENT, **change})


def test_staging_reuses_bytes_and_isolates_failures(
    runtime: SimpleNamespace, http: SimpleNamespace
) -> None:
    runtime.context["mda_channel"]["raw_event"]["files"] += [
        {**ATTACHMENT, "id": "F2", "url_private": "https://evil.test"},
        {**ATTACHMENT, "id": "F3"},
    ]
    middleware = SlackFileTransferMiddleware()
    result = asyncio.run(middleware.abefore_agent({}, runtime))["messages"][0]
    assert result.id == "attachments:delivery-1"
    assert "not instructions" in result.content
    assert "secret" not in result.content
    assert (
        runtime.backend.download_files([f"{files.ATTACHMENTS_DIR}/F1__data.bin"])[0].content
        == BINARY
    )
    assert "Attachment 2: File URL is not allowed." in result.content
    asyncio.run(middleware.abefore_agent({}, runtime))
    assert len(http.calls) == 2
    runtime.server_info.user["mda_source_thread_id"] = "other"
    assert asyncio.run(middleware.abefore_agent({}, runtime)) is None
    runtime.context["mda_channel"]["source_thread_key"] = "other"
    runtime.context["mda_channel"]["target"]["source_thread_key"] = "other"
    runtime.backend = None
    assert (
        "No workspace backend"
        in asyncio.run(middleware.abefore_agent({}, runtime))["messages"][0].content
    )


@pytest.mark.parametrize("current", [False, True])
def test_thread_history_files_are_staged_once(
    runtime: SimpleNamespace, http: SimpleNamespace, current: bool
) -> None:
    runtime.context["mda_channel"]["raw_event"] = (
        {"files": [ATTACHMENT]} if current else {"text": "you get that?"}
    )
    history_file = {**ATTACHMENT, "id": "F2"}
    http.history_pages = [
        {
            "ok": True,
            "messages": [{"files": [ATTACHMENT, history_file] if current else [history_file]}],
            "response_metadata": {"next_cursor": "next+/="},
        },
        {"ok": True, "messages": [{"files": [history_file]}]},
    ]
    status = asyncio.run(files.stage_files(runtime))
    assert status is not None
    assert status.content.count("F2__data.bin") == 1
    assert (
        runtime.backend.download_files([f"{files.ATTACHMENTS_DIR}/F2__data.bin"])[0].content
        == BINARY
    )
    assert http.history_calls == [
        {"channel": "C1", "ts": "123.456", "limit": "100"},
        {"channel": "C1", "ts": "123.456", "limit": "100", "cursor": "next+/="},
    ]
    http.history_calls.clear()
    asyncio.run(files.stage_files(runtime))
    assert len(http.calls) == (2 if current else 1)


@pytest.mark.parametrize("error", ["missing_scope", "ratelimited"])
def test_history_error_keeps_current_files(
    runtime: SimpleNamespace, http: SimpleNamespace, error: str
) -> None:
    http.history_pages = [{"ok": False, "error": error}]
    status = asyncio.run(files.stage_files(runtime))
    assert "Slack thread history could not be read" in status.content
    assert "F1__data.bin" in status.content
    assert error not in status.content


def test_history_error_keeps_earlier_pages(runtime: SimpleNamespace, http: SimpleNamespace) -> None:
    http.history_pages = [
        {
            "ok": True,
            "messages": [{"files": [{**ATTACHMENT, "id": "F2"}]}],
            "response_metadata": {"next_cursor": "next+/="},
        },
        httpx.ReadError("private response"),
    ]
    status = asyncio.run(files.stage_files(runtime))
    assert "Slack thread history could not be read" in status.content
    assert "F2__data.bin" in status.content
    assert "private response" not in status.content


def test_history_scan_is_bounded(runtime: SimpleNamespace, http: SimpleNamespace) -> None:
    http.history_pages = [
        {"ok": True, "messages": [], "response_metadata": {"next_cursor": "more"}}
    ]
    status = asyncio.run(files.stage_files(runtime))
    assert "Slack thread history scan limit reached" in status.content
    assert len(http.history_calls) == 5


@pytest.mark.parametrize("mode", ["resume", "invalid-target", "forged-source"])
def test_history_requires_message_and_verified_target(
    runtime: SimpleNamespace, http: SimpleNamespace, mode: str
) -> None:
    channel = runtime.context["mda_channel"]
    channel.pop("raw_event")
    if mode == "resume":
        channel["event"] = {"type": "interrupt_resume", "resume": {}}
    elif mode == "invalid-target":
        channel["target"]["address"]["thread_id"] = "invalid"
    else:
        runtime.server_info.user["mda_source_thread_id"] = "other"
    assert asyncio.run(files.stage_files(runtime)) is None
    assert http.history_calls == []


@pytest.mark.parametrize("missing", ["source", "backend"])
def test_visibility_and_execution_recheck(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    middleware = SlackFileTransferMiddleware()
    request = ModelRequest(model=FileModel(), messages=[], tools=middleware.tools, runtime=runtime)
    assert files.can_upload_files(runtime)
    if missing == "source":
        runtime.server_info.user = {"mda_user_id": "api-user"}
    elif missing == "backend":
        runtime.backend = None
    assert asyncio.run(prepare_request(request)).tools == []
    assert (
        asyncio.run(files.upload_files(["result.bin"], runtime))
        == "File attachments are unavailable for this run."
    )


def test_tool_visibility_does_not_fetch_credentials(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def unavailable(slug: str, owner: object) -> str:
        calls.append(slug)
        raise RuntimeError("private response")

    monkeypatch.setattr(connections, "get", unavailable)
    middleware = SlackFileTransferMiddleware()
    request = ModelRequest(model=FileModel(), messages=[], tools=middleware.tools, runtime=runtime)
    for _ in range(2):
        assert asyncio.run(prepare_request(request)).tools == middleware.tools
    assert calls == []


@pytest.mark.parametrize("mode", ["history", "incoming", "outgoing"])
def test_missing_credential_fails_transfers(
    runtime: SimpleNamespace, http: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def unavailable(slug: str, owner: object) -> str:
        raise RuntimeError("private response")

    monkeypatch.setattr(connections, "get", unavailable)
    if mode == "incoming":
        runtime.context["mda_channel"]["event"] = {"type": "interrupt_resume", "resume": {}}
    with pytest.raises(
        RuntimeError, match=r"^The managed Slack connection is unavailable\. Reconnect Slack\.$"
    ):
        asyncio.run(
            files.upload_files(["result.bin"], runtime)
            if mode == "outgoing"
            else files.stage_files(runtime)
        )
    assert http.calls == []
    assert http.history_calls == []


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "denied",
        "redirect",
        "malformed",
        "network",
        "missing_scope",
        "api_redirect",
        "api_network",
    ],
)
def test_upload_bytes_and_completion(
    runtime: SimpleNamespace, http: SimpleNamespace, failure: str | None
) -> None:
    runtime.backend.upload_files([("/workspace/result.bin", BINARY)])
    http.failure = failure
    result = asyncio.run(files.upload_files(["result.bin"], runtime))
    if failure in ("missing_scope", "api_redirect", "api_network"):
        assert ("files:write" in result) == (failure == "missing_scope")
        if failure != "missing_scope":
            assert result == "File upload failed."
        assert len(http.calls) == 1
    else:
        assert result == (files.DELIVERY_NOT_CONFIRMED if failure else "Files uploaded.")
        assert len(http.calls) == 3
    assert "secret" not in result


def test_transfer_limits_and_download_redirects(
    http: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 1)
    with pytest.raises(files.SlackError, match=f"{files.MAX_FILE_BYTES / (1024 * 1024):g} MiB"):
        asyncio.run(files.download_file(ATTACHMENT["url_private"], "secret"))
    with pytest.raises(files.SlackError, match=f"{files.MAX_FILE_BYTES / (1024 * 1024):g} MiB"):
        asyncio.run(files._prepare_upload(BINARY, "result.bin", "secret"))
    http.failure = "download_redirect"
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(files.download_file(ATTACHMENT["url_private"], "secret"))


def test_credentials_use_fresh_agent_connection_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def get(slug: str, owner: object) -> str:
        calls.append((slug, owner))
        return str(len(calls))

    monkeypatch.setattr(connections, "get", get)
    assert asyncio.run(files.get_slack_bot_token()) != asyncio.run(files.get_slack_bot_token())
    assert calls == [("mda/slack-bot-token", {"type": "agent"})] * 2


class FileModel(BaseChatModel):
    tool_names: list[str] = Field(default_factory=list)
    prompts: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "file-test"

    def bind_tools(self, tools: list[object], **kwargs: object) -> "FileModel":
        self.tool_names = [getattr(entry, "name", "") for entry in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.prompts.append(messages)
        calls = (
            []
            if "upload_files" not in self.tool_names
            or any(isinstance(message, ToolMessage) for message in messages)
            else [
                {
                    "name": "upload_files",
                    "args": {"paths": ["result.bin"]},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ]
        )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="done", tool_calls=calls))]
        )


class ApiContext(BaseModel):
    channel: str


@pytest.mark.parametrize(
    "mode", ["slack", "history", "resume", "api", "undeclared", "missing", "revoked"]
)
@pytest.mark.parametrize("use_async", [False, True])
def test_native_graph(
    runtime: SimpleNamespace,
    http: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    use_async: bool,
) -> None:
    backend = runtime.backend
    backend.bind_runtime = lambda _: backend
    backend.upload_files([("/workspace/result.bin", BINARY)])
    monkeypatch.setattr(runtime_module, "_managed_sandbox_backend", lambda *args, **kwargs: backend)
    context, user = runtime.context, runtime.server_info.user
    if mode == "history":
        context["mda_channel"].pop("raw_event")
        http.history_pages = [{"ok": True, "messages": [{"files": [ATTACHMENT]}]}]
    elif mode in ("resume", "revoked"):
        context["mda_channel"].pop("raw_event")
        context["mda_channel"]["event"] = {"type": "interrupt_resume", "resume": {}}
    elif mode in ("api", "undeclared"):
        context, user = {"channel": "authored-value"}, {"mda_user_id": "api-user"}
    if mode not in ("api", "undeclared"):
        context = {"channel": "application-value", "mda_channel": context["mda_channel"]}
    model = FileModel()
    graph = runtime_module.compile_managed_agent(
        DeepAgentDefinition({"model": model, "context_schema": ApiContext}),
        {"configurable": {"thread_id": "graph", "langgraph_auth_user": user}},
        channels=[] if mode == "undeclared" else [SimpleNamespace(kind="slack", name="slack")],
        sandbox=SimpleNamespace(kind="test", options={}),
        run_context=context,
    )
    state = {"messages": [{"role": "user", "content": "hello"}]}
    if mode in ("missing", "revoked"):

        async def unavailable(slug: str, owner: object) -> str:
            raise RuntimeError("private response")

        monkeypatch.setattr(connections, "get", unavailable)
        with pytest.raises(RuntimeError, match="The managed Slack connection is unavailable"):
            (
                asyncio.run(graph.ainvoke(state, context=context))
                if use_async
                else graph.invoke(state, context=context)
            )
        assert len(model.prompts) == (1 if mode == "revoked" else 0)
        assert http.calls == []
        assert http.history_calls == []
        return
    result = (
        asyncio.run(graph.ainvoke(state, context=context))
        if use_async
        else graph.invoke(state, context=context)
    )
    assert result["messages"][-1].content == "done"
    assert "attach_file" not in model.tool_names
    assert ("upload_files" in model.tool_names) == (mode in ("slack", "history", "resume"))
    assert (
        len(http.calls) == {"slack": 4, "history": 4, "resume": 3, "api": 0, "undeclared": 0}[mode]
    )
    if mode in ("slack", "history"):
        assert any("Attachment 1:" in str(message.content) for message in model.prompts[0])


def test_download_permission_error_is_actionable(http: SimpleNamespace) -> None:
    http.failure = "download_denied"
    with pytest.raises(files.SlackError, match="files:read") as error:
        asyncio.run(files.download_file(ATTACHMENT["url_private"], "secret"))
    assert "secret" not in str(error.value)


@pytest.fixture
def batch_http(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        calls=[],
        uploaded=[],
        completions=[],
        failure=None,
    )
    first_upload = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        state.calls.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "files.getUploadURLExternal":
            filename = request.url.params["filename"]
            file_id = f"F{filename.split('-', 1)[0]}"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "file_id": file_id,
                    "upload_url": f"https://files.slack.com/{file_id}",
                },
            )
        if request.url.host == "files.slack.com":
            assert "authorization" not in request.headers
            assert request.content == BINARY
            if method == "F1":
                await first_upload.wait()
            state.uploaded.append(method)
            if method == "F2":
                first_upload.set()
            return httpx.Response(403 if state.failure == "bytes" else 200)
        assert request.headers["authorization"] == "Bearer secret"
        if method == "files.completeUploadExternal":
            state.completions.append(json.loads(request.content))
            response = {"ok": state.failure != "complete", "error": "secret"}
            return httpx.Response(200, json=response)
        raise AssertionError(f"Unexpected endpoint: {method}")

    client = httpx.AsyncClient
    monkeypatch.setattr(
        files.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return state


def test_batch_completion_preserves_order_when_bytes_finish_in_reverse_order(
    runtime: SimpleNamespace,
    batch_http: SimpleNamespace,
) -> None:
    paths = ["1-eric.mp3", "2-brian.mp3"]
    runtime.backend.upload_files([(f"/workspace/{path}", BINARY) for path in paths])
    result = asyncio.run(files.upload_files(paths, runtime))
    assert batch_http.uploaded == ["F2", "F1"]
    assert batch_http.completions == [
        {
            "files": [{"id": "F1", "title": paths[0]}, {"id": "F2", "title": paths[1]}],
            "channel_id": "C1",
            "thread_ts": "123.456",
        }
    ]
    assert result == "Files uploaded."
    assert len(batch_http.calls) == 5


@pytest.mark.parametrize("paths", [[], ["a"] * 11, ["ok", "../private"], ["a", "/workspace/a"]])
def test_batch_rejects_invalid_paths_before_network_access(
    runtime: SimpleNamespace,
    batch_http: SimpleNamespace,
    paths: list[str],
) -> None:
    result = asyncio.run(files.upload_files(paths, runtime))
    assert result != "Files uploaded."
    assert batch_http.calls == []


@pytest.mark.parametrize("failure", ["bytes", "complete"])
def test_batch_failure_does_not_retry_or_claim_delivery(
    runtime: SimpleNamespace,
    batch_http: SimpleNamespace,
    failure: str,
) -> None:
    runtime.backend.upload_files(
        [(f"/workspace/{name}", BINARY) for name in ["1-eric.mp3", "2-brian.mp3"]]
    )
    batch_http.failure = failure
    result = asyncio.run(files.upload_files(["1-eric.mp3", "2-brian.mp3"], runtime))
    assert "secret" not in result
    assert result != "Files uploaded."
    assert len(batch_http.completions) == (0 if failure == "bytes" else 1)
    if failure != "bytes":
        assert "Do not retry" in result


def test_batch_accepts_one_file(runtime: SimpleNamespace, batch_http: SimpleNamespace) -> None:
    runtime.backend.upload_files([("/workspace/2-brian.mp3", BINARY)])
    result = asyncio.run(files.upload_files(["2-brian.mp3"], runtime))
    assert result == "Files uploaded."
    assert len(batch_http.completions) == 1


def test_batch_rechecks_trusted_destination_before_network_access(
    runtime: SimpleNamespace,
    batch_http: SimpleNamespace,
) -> None:
    runtime.server_info.user = {"mda_user_id": "api-caller"}
    result = asyncio.run(files.upload_files(["2-brian.mp3"], runtime))
    assert result == "File attachments are unavailable for this run."
    assert batch_http.calls == []


def test_batch_preserves_order_across_transfer_groups(
    runtime: SimpleNamespace,
    batch_http: SimpleNamespace,
) -> None:
    paths = [f"{index}-voice.mp3" for index in range(1, 11)]
    runtime.backend.upload_files([(f"/workspace/{path}", BINARY) for path in paths])
    result = asyncio.run(files.upload_files(paths, runtime))
    assert [file["title"] for file in batch_http.completions[0]["files"]] == paths
    assert len(batch_http.completions) == 1
    assert result == "Files uploaded."
    assert len(batch_http.calls) == 21


def test_only_upload_files_is_registered() -> None:
    assert [tool.name for tool in SlackFileTransferMiddleware().tools] == ["upload_files"]
