import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import httpx2
import pytest

from openswe.slack import client as slack_client
from openswe.slack import staged_files
from openswe.utils import url_safety


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
        lambda **kwargs: async_client(**{**kwargs, "transport": httpx2.MockTransport(handle)}),
    )
    monkeypatch.setattr(
        url_safety,
        "resolve_and_validate",
        lambda url: (urlparse(url).hostname, ["1.1.1.1"]),
    )
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(slack_client, "SLACK_FILE_DOWNLOAD_MAX_BYTES", 3)
    return responses, requests


def _entry(name: str, url: str) -> staged_files.SlackFileEntry:
    return staged_files.SlackFileEntry(name=name, url=url)


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

    assert await slack_client.download_slack_file("https://files.slack.com/a.zip") == b"zip"
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
    with pytest.raises(slack_client.SlackFileDownloadError, match="file_too_large"):
        await slack_client.download_slack_file("https://files.slack.com/a/x.zip")
    assert stream.read_count == 2
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
        with pytest.raises(slack_client.SlackFileDownloadError, match="download_failed"):
            await slack_client.download_slack_file("https://files.slack.com/a.zip")
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

    def public_host(url: str) -> tuple[str, list[str]]:
        if "127.0.0.1" in url:
            raise url_safety.UnsafeUrlError(url, "private host")
        return resolve(url)

    monkeypatch.setattr(url_safety, "resolve_and_validate", public_host)

    with pytest.raises(slack_client.SlackFileDownloadError, match="unsafe_download_url"):
        await slack_client.download_slack_file("https://files.slack.com/a.zip")
    assert len(requests) == 1
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.asyncio
async def test_slack_files_wait_for_the_runs_sandbox_and_land_there_once(
    monkeypatch, tmp_path
) -> None:
    """The webhook only records the files: MDA creates the thread's box when its run starts."""
    from deepagents.backends.protocol import FileUploadResponse

    from openswe.sandboxes import state

    metadata: dict[str, object] = {}

    async def read_metadata(_thread_id: str) -> dict[str, object]:
        return dict(metadata)

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    stored = metadata
    client = MagicMock()
    client.threads.update = write_metadata
    monkeypatch.setattr(state, "get_sandbox_metadata", read_metadata)
    monkeypatch.setattr(staged_files, "get_client", lambda: client)

    async def download(url: str) -> bytes:
        if url.endswith("second.zip"):
            assert (tmp_path / "bundle.zip").read_bytes() == b"zip"
        return b"zip"

    async def upload(files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        for path, content in files:
            (tmp_path / path.rsplit("/", 1)[-1]).write_bytes(content)
        return [FileUploadResponse(path=path) for path, _ in files]

    backend = MagicMock()
    backend.aupload_files = AsyncMock(side_effect=upload)
    monkeypatch.setattr(slack_client, "download_slack_file", download)

    first = await staged_files.queue_slack_files(
        "thread-1",
        [
            _entry("bundle.zip", "https://files.slack.com/first.zip"),
            _entry("bundle.zip", "https://files.slack.com/second.zip"),
        ],
    )
    # A later message before the run starts names its files apart from those waiting.
    later = await staged_files.queue_slack_files(
        "thread-1", [_entry("bundle.zip", "https://files.slack.com/third.zip")]
    )
    backend.aupload_files.assert_not_awaited()

    run_read = dict(metadata)
    # A message queued after the run read the thread waits for the next run, not lost.
    racing = await staged_files.queue_slack_files(
        "thread-1", [_entry("late.csv", "https://files.slack.com/late.csv")]
    )
    await staged_files.deliver_slack_files("thread-1", run_read, backend)
    assert metadata[staged_files.PENDING_SLACK_FILES_KEY] == [
        {"url": "https://files.slack.com/late.csv", "path": racing[0].sandbox_path}
    ]
    await staged_files.deliver_slack_files("thread-1", dict(metadata), backend)

    assert [*first, *later] == [
        staged_files.StagedSlackFile("bundle.zip", "/workspace/.open-swe/slack-files/bundle.zip"),
        staged_files.StagedSlackFile("bundle-1", "/workspace/.open-swe/slack-files/bundle-1"),
        staged_files.StagedSlackFile("bundle-2", "/workspace/.open-swe/slack-files/bundle-2"),
    ]
    assert backend.aupload_files.await_count == 4
    assert (tmp_path / "bundle-2").read_bytes() == b"zip"
    assert metadata[staged_files.PENDING_SLACK_FILES_KEY] is None


@pytest.mark.asyncio
async def test_download_slack_file_requires_a_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from openswe.slack import client as slack_client
    from openswe.slack.http import SlackConfigurationError

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    with pytest.raises(SlackConfigurationError, match="SLACK_BOT_TOKEN is required"):
        await slack_client.download_slack_file("https://files.slack.com/a/x.zip")
