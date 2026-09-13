import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import httpx2
import pytest

from agent.slack import client as slack_client
from agent.slack import webhook as slack_webhook
from agent.utils import url_safety


class DownloadStream(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes | BaseException]) -> None:
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.read_count += 1
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def download_http(monkeypatch: pytest.MonkeyPatch):
    responses: list[httpx2.Response] = []
    requests: list[httpx2.Request] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return responses.pop(0)

    async_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: async_client(**kwargs, transport=httpx2.MockTransport(handle)),
    )
    monkeypatch.setattr(
        url_safety,
        "resolve_and_validate",
        lambda url: (True, "", urlparse(url).hostname, [(None, None, None, None, ("1.1.1.1", 0))]),
    )
    monkeypatch.setattr(slack_client, "SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(slack_client, "SLACK_FILE_DOWNLOAD_MAX_BYTES", 3)
    return responses, requests


def _file(name: str, url: str, mimetype: str = "application/zip") -> dict[str, Any]:
    return {"id": name, "name": name, "url_private": url, "mimetype": mimetype, "size": 3}


@pytest.mark.asyncio
async def test_download_slack_file_resolves_url_with_files_info(
    monkeypatch: pytest.MonkeyPatch, download_http
) -> None:
    responses, _ = download_http
    responses.append(httpx2.Response(200, content=b"zip"))
    calls: list[str] = []

    class FakeClient:
        async def files_info(self, *, file: str) -> dict[str, Any]:
            calls.append(file)
            return {"file": {"url_private": "https://files.slack.com/a/bundle.zip"}}

    @asynccontextmanager
    async def fake_slack_client(**_: Any):
        yield FakeClient()

    monkeypatch.setattr(slack_client, "slack_client", fake_slack_client)

    assert await slack_client.download_slack_file("F123") == (b"zip", None)
    assert calls == ["F123"]


def test_slack_file_entries_skips_images_and_dedupes_by_url() -> None:
    messages = [
        {
            "files": [
                _file("bundle.zip", "https://files.slack.com/a/bundle.zip"),
                _file("shot.png", "https://files.slack.com/a/shot.png", "image/png"),
                {"name": "no-url.txt"},
            ]
        },
        {"files": [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")]},
    ]

    entries = slack_webhook._slack_file_entries(messages)

    assert [entry["name"] for entry in entries] == ["bundle.zip", "shot.png"]


def test_slack_file_entries_caps_attachment_count() -> None:
    files = [
        _file(f"f{index}.zip", f"https://files.slack.com/a/f{index}.zip") for index in range(25)
    ]

    entries = slack_webhook._slack_file_entries([{"files": files}])

    assert len(entries) == slack_webhook._MAX_SLACK_FILE_ATTACHMENTS


@pytest.mark.parametrize(
    ("name", "url", "expected"),
    [
        ("bundle.zip", "https://files.slack.com/a/x", "bundle.zip"),
        ("../../etc/passwd", "https://files.slack.com/a/fallback.zip", "fallback.zip"),
        ("", "https://files.slack.com/a/from-url.tar.gz", "from-url.tar.gz"),
        ("weird name!.txt", "https://files.slack.com/a/x", "weird_name_.txt"),
    ],
)
def test_sanitize_slack_filename(name: str, url: str, expected: str) -> None:
    assert slack_webhook._sanitize_slack_filename(name, url) == expected


@pytest.mark.asyncio
async def test_download_slack_files_stages_into_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[{"error": None}])
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(
        slack_webhook.slack_utils, "download_slack_file", AsyncMock(return_value=(b"zip", None))
    )

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")], "thread-1"
    )

    assert staged == [
        ("bundle.zip", f"{slack_webhook._SLACK_FILE_DIR}/bundle.zip", "application/zip", 3)
    ]
    uploaded_path, uploaded_content = backend.aupload_files.await_args.args[0][0]
    assert uploaded_path == f"{slack_webhook._SLACK_FILE_DIR}/bundle.zip"
    assert uploaded_content == b"zip"


@pytest.mark.asyncio
async def test_download_slack_files_skips_failed_download(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[{"error": None}])
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(
        slack_webhook.slack_utils,
        "download_slack_file",
        AsyncMock(side_effect=[(None, "file_too_large"), (b"ok", None)]),
    )

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [
            _file("huge.zip", "https://files.slack.com/a/huge.zip"),
            _file("small.zip", "https://files.slack.com/a/small.zip"),
        ],
        "thread-1",
    )

    assert staged == [
        ("small.zip", f"{slack_webhook._SLACK_FILE_DIR}/small.zip", "application/zip", 2)
    ]


@pytest.mark.asyncio
async def test_download_slack_files_reports_missing_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[{"error": None}])
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(
        slack_webhook.slack_utils,
        "download_slack_file",
        AsyncMock(return_value=(None, "missing_scope:files:read")),
    )
    failures: list[str] = []

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")],
        "thread-1",
        failures=failures,
    )

    assert staged == []
    assert failures == [
        "Slack could not download an attachment because the app is missing the `files:read` "
        "scope. Add that scope under OAuth & Permissions and reinstall the Slack app in this "
        "workspace."
    ]


@pytest.mark.asyncio
async def test_download_slack_files_tolerates_unreachable_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread",
        AsyncMock(side_effect=RuntimeError("sandbox unreachable")),
    )

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")], "thread-1"
    )

    assert staged == []


@pytest.mark.asyncio
async def test_download_slack_file_skips_redirect_body_and_drops_token(download_http) -> None:
    responses, requests = download_http
    redirect = DownloadStream([b"do not read this redirect body"])
    payload = DownloadStream([b"zip"])
    responses.extend(
        [
            httpx2.Response(302, headers={"Location": "https://cdn.example/file"}, stream=redirect),
            httpx2.Response(200, stream=payload),
        ]
    )

    assert await slack_client.download_slack_file("https://files.slack.com/a.zip") == (b"zip", None)
    assert redirect.read_count == 0
    assert redirect.closed and payload.closed
    assert requests[0].headers["Authorization"] == "Bearer test-slack-token"
    assert "Authorization" not in requests[1].headers
    assert [request.headers["Host"] for request in requests] == ["files.slack.com", "cdn.example"]
    assert [request.extensions["sni_hostname"] for request in requests] == [
        "files.slack.com",
        "cdn.example",
    ]
    assert all(request.url.host == "1.1.1.1" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [None, "invalid", "1"])
async def test_download_slack_file_stops_reading_oversize_payload(
    download_http, content_length
) -> None:
    responses, _ = download_http
    stream = DownloadStream([b"ab", b"cd", b"must not be read"])
    headers = {"Content-Length": content_length} if content_length is not None else {}
    responses.append(httpx2.Response(200, headers=headers, stream=stream))
    assert await slack_client.download_slack_file("https://files.slack.com/a/x.zip") == (
        None,
        "file_too_large",
    )
    assert stream.read_count == 2
    assert stream.closed


@pytest.mark.asyncio
async def test_download_slack_file_rejects_large_content_length_without_reading(
    download_http,
) -> None:
    responses, _ = download_http
    stream = DownloadStream([b"oversized"])
    responses.append(httpx2.Response(200, headers={"Content-Length": "9"}, stream=stream))

    assert await slack_client.download_slack_file("https://files.slack.com/a.zip") == (
        None,
        "file_too_large",
    )
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"", b"zip"])
async def test_download_slack_file_accepts_payload_up_to_limit(download_http, payload) -> None:
    responses, _ = download_http
    stream = DownloadStream([payload[:1], payload[1:]])
    responses.append(httpx2.Response(200, stream=stream))

    assert await slack_client.download_slack_file("https://files.slack.com/a.zip") == (
        payload,
        None,
    )
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx2.ReadError("interrupted"), asyncio.CancelledError()])
async def test_download_slack_file_closes_interrupted_stream(download_http, failure) -> None:
    responses, _ = download_http
    stream = DownloadStream([b"a", failure])
    responses.append(httpx2.Response(200, stream=stream))

    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await slack_client.download_slack_file("https://files.slack.com/a.zip")
    else:
        assert await slack_client.download_slack_file("https://files.slack.com/a.zip") == (
            None,
            "download_failed",
        )
    assert stream.closed


@pytest.mark.asyncio
async def test_download_slack_file_closes_redirect_before_blocking_private_host(
    download_http, monkeypatch
) -> None:
    responses, requests = download_http
    stream = DownloadStream([b"unused"])
    responses.append(
        httpx2.Response(302, headers={"Location": "http://127.0.0.1/file"}, stream=stream)
    )
    resolve = url_safety.resolve_and_validate
    monkeypatch.setattr(
        url_safety,
        "resolve_and_validate",
        lambda url: (False, "private host", None, None) if "127.0.0.1" in url else resolve(url),
    )

    assert await slack_client.download_slack_file("https://files.slack.com/a.zip") == (
        None,
        "unsafe_download_url",
    )
    assert len(requests) == 1
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_download_slack_files_uploads_before_downloading_next_file(
    monkeypatch, tmp_path
) -> None:
    async def download(url: str) -> tuple[bytes, None]:
        if url.endswith("second.zip"):
            assert (tmp_path / "bundle.zip").read_bytes() == b"zip"
        return b"zip", None

    async def upload(files: list[tuple[str, bytes]]) -> list[dict[str, None]]:
        for path, content in files:
            (tmp_path / path.rsplit("/", 1)[-1]).write_bytes(content)
        return [{"error": None} for _ in files]

    backend = MagicMock()
    backend.aupload_files = upload
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(slack_client, "download_slack_file", download)

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [
            _file("bundle.zip", "https://files.slack.com/first.zip"),
            _file("bundle.zip", "https://files.slack.com/second.zip"),
        ],
        "thread-1",
    )

    assert staged == [
        ("bundle.zip", "/workspace/.open-swe/slack-files/bundle.zip", "application/zip", 3),
        ("bundle-1", "/workspace/.open-swe/slack-files/bundle-1", "application/zip", 3),
    ]
    assert (tmp_path / "bundle-1").read_bytes() == b"zip"


@pytest.mark.asyncio
async def test_download_slack_file_requires_a_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.slack import client as slack_client

    monkeypatch.setattr(slack_client, "SLACK_BOT_TOKEN", "")

    assert await slack_client.download_slack_file("https://files.slack.com/a/x.zip") == (
        None,
        "missing_slack_bot_token",
    )
