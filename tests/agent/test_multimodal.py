from __future__ import annotations

import socket
from typing import Any, cast
from urllib.parse import urlparse

import httpx

import agent.utils.multimodal as multimodal
import agent.utils.url_safety as url_safety
from agent.utils.multimodal import (
    extract_image_urls,
    fetch_image_block,
    vision_not_supported_warning,
)


def test_extract_image_urls_empty() -> None:
    assert extract_image_urls("") == []


def test_extract_image_urls_markdown_and_direct_dedupes() -> None:
    text = (
        "Here is an image ![alt](https://example.com/a.png) and another "
        "![https://example.com/b.JPG?size=large plus a repeat https://example.com/a.png"
    )

    assert extract_image_urls(text) == [
        "https://example.com/a.png",
        "https://example.com/b.JPG?size=large",
    ]


def test_extract_image_urls_ignores_non_images() -> None:
    text = "Not images: https://example.com/file.pdf and https://example.com/noext"

    assert extract_image_urls(text) == []


def test_extract_image_urls_markdown_syntax() -> None:
    text = "Check out this screenshot: ![Screenshot](https://example.com/screenshot.png)"

    assert extract_image_urls(text) == ["https://example.com/screenshot.png"]


def test_extract_image_urls_direct_links() -> None:
    text = "Direct link: https://example.com/photo.jpg and another https://example.com/image.gif"

    assert extract_image_urls(text) == [
        "https://example.com/photo.jpg",
        "https://example.com/image.gif",
    ]


def test_extract_image_urls_various_formats() -> None:
    text = (
        "Multiple formats: "
        "https://example.com/image.png "
        "https://example.com/photo.jpeg "
        "https://example.com/pic.gif "
        "https://example.com/img.webp "
        "https://example.com/bitmap.bmp "
        "https://example.com/scan.tiff"
    )

    assert extract_image_urls(text) == [
        "https://example.com/image.png",
        "https://example.com/photo.jpeg",
        "https://example.com/pic.gif",
        "https://example.com/img.webp",
        "https://example.com/bitmap.bmp",
        "https://example.com/scan.tiff",
    ]


def test_extract_image_urls_with_query_params() -> None:
    text = "Image with params: https://cdn.example.com/image.png?width=800&height=600"

    assert extract_image_urls(text) == ["https://cdn.example.com/image.png?width=800&height=600"]


def test_extract_image_urls_case_insensitive() -> None:
    text = "Mixed case: https://example.com/Image.PNG and https://example.com/photo.JpEg"

    assert extract_image_urls(text) == [
        "https://example.com/Image.PNG",
        "https://example.com/photo.JpEg",
    ]


def test_extract_image_urls_deduplication() -> None:
    text = "Same URL twice: https://example.com/image.png and again https://example.com/image.png"

    assert extract_image_urls(text) == ["https://example.com/image.png"]


def test_extract_image_urls_mixed_markdown_and_direct() -> None:
    text = (
        "Markdown: ![alt text](https://example.com/markdown.png) "
        "and direct: https://example.com/direct.jpg "
        "and another markdown ![](https://example.com/another.gif)"
    )

    result = extract_image_urls(text)
    assert set(result) == {
        "https://example.com/markdown.png",
        "https://example.com/direct.jpg",
        "https://example.com/another.gif",
    }
    assert len(result) == 3


def test_vision_not_supported_warning_includes_model_and_count() -> None:
    warning = vision_not_supported_warning("fireworks:.../glm-5p2", 2)
    assert "glm-5p2" in warning
    assert "2 image(s)" in warning
    assert "does not support image input" in warning


def test_vision_not_supported_warning_singular() -> None:
    warning = vision_not_supported_warning("fireworks:.../glm-5p2", 1)
    assert "1 image(s)" in warning


def _addr_info(ip: str, port: int | None = None) -> tuple:
    return (
        socket.AF_INET,
        socket.SOCK_STREAM,
        6,
        "",
        (ip, port or 0),
    )


class FakeImageResponse:
    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} error",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code, request=httpx.Request("GET", self.url)),
            )


class FakeImageClient:
    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responder(method, url, **kwargs)


def _patch_image_dns(monkeypatch: Any) -> None:
    def fake_getaddrinfo(host: str, port: int | None, *args: Any, **kwargs: Any) -> list[tuple]:
        public_hosts = {
            "cdn.example.com",
            "example.com",
            "files.slack.com",
            "private.files.slack.com",
        }
        ip = "93.184.216.34" if host in public_hosts else host
        return [_addr_info(ip, port)]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)


async def test_fetch_image_block_blocks_redirect_to_internal_url(monkeypatch: Any) -> None:
    _patch_image_dns(monkeypatch)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        return FakeImageResponse(
            status_code=302,
            url=url,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

    client = FakeImageClient(responder)

    result = await fetch_image_block(
        "https://example.com/start.png", cast(httpx.AsyncClient, client)
    )

    assert result is None
    assert len(client.calls) == 1
    assert urlparse(client.calls[0]["url"]).hostname == "93.184.216.34"
    assert client.calls[0]["headers"]["Host"] == "example.com"


async def test_fetch_image_block_tries_each_validated_address(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        url_safety.socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            _addr_info("93.184.216.34", port),
            _addr_info("93.184.216.35", port),
        ],
    )
    monkeypatch.setattr(multimodal, "create_image_block", lambda **kwargs: kwargs)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        if urlparse(url).hostname == "93.184.216.34":
            raise httpx.ConnectError("address unreachable")
        return FakeImageResponse(
            status_code=200,
            url=url,
            headers={"Content-Type": "image/png"},
            content=b"png",
        )

    client = FakeImageClient(responder)

    result = await fetch_image_block(
        "https://example.com/image.png", cast(httpx.AsyncClient, client)
    )

    assert result == {"base64": "cG5n", "mime_type": "image/png"}
    assert [urlparse(call["url"]).hostname for call in client.calls] == [
        "93.184.216.34",
        "93.184.216.35",
    ]


async def test_fetch_image_block_accepts_image_at_size_limit(monkeypatch: Any) -> None:
    _patch_image_dns(monkeypatch)
    monkeypatch.setattr(multimodal, "_MAX_IMAGE_BYTES", 3)
    monkeypatch.setattr(multimodal, "create_image_block", lambda **kwargs: kwargs)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        return FakeImageResponse(
            status_code=200,
            url=url,
            headers={"Content-Type": "image/png"},
            content=b"png",
        )

    result = await fetch_image_block(
        "https://example.com/image.png", cast(httpx.AsyncClient, FakeImageClient(responder))
    )

    assert result == {"base64": "cG5n", "mime_type": "image/png"}


async def test_fetch_image_block_warns_about_image_above_size_limit(monkeypatch: Any) -> None:
    _patch_image_dns(monkeypatch)
    monkeypatch.setattr(multimodal, "_MAX_IMAGE_BYTES", 3)
    monkeypatch.setattr(multimodal, "create_image_block", lambda **kwargs: kwargs)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        return FakeImageResponse(
            status_code=200,
            url=url,
            headers={"Content-Type": "image/png"},
            content=b"large",
        )

    result = await fetch_image_block(
        "https://example.com/image.png", cast(httpx.AsyncClient, FakeImageClient(responder))
    )

    assert result is not None
    assert result["type"] == "text"
    assert result["text"] == (
        "An attached image was skipped because it exceeded the 10 MiB size limit."
    )


async def test_fetch_image_block_does_not_forward_slack_auth_to_redirect_host(
    monkeypatch: Any,
) -> None:
    _patch_image_dns(monkeypatch)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(multimodal, "create_image_block", lambda **kwargs: kwargs)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        host = kwargs["headers"]["Host"]
        if host == "files.slack.com":
            return FakeImageResponse(
                status_code=302,
                url=url,
                headers={"Location": "https://cdn.example.com/image.png"},
            )
        return FakeImageResponse(
            status_code=200,
            url=url,
            headers={"Content-Type": "image/png"},
            content=b"png",
        )

    client = FakeImageClient(responder)

    result = await fetch_image_block(
        "https://files.slack.com/image.png", cast(httpx.AsyncClient, client)
    )

    assert result == {"base64": "cG5n", "mime_type": "image/png"}
    assert len(client.calls) == 2
    assert client.calls[0]["headers"]["Authorization"] == "Bearer test-slack-token"
    assert "Authorization" not in client.calls[1]["headers"]
    assert client.calls[1]["headers"]["Host"] == "cdn.example.com"


async def test_fetch_image_block_does_not_add_slack_auth_after_untrusted_redirect(
    monkeypatch: Any,
) -> None:
    _patch_image_dns(monkeypatch)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(multimodal, "create_image_block", lambda **kwargs: kwargs)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        if kwargs["headers"]["Host"] == "example.com":
            return FakeImageResponse(
                status_code=302,
                url=url,
                headers={"Location": "https://files.slack.com/private.png"},
            )
        return FakeImageResponse(
            status_code=200,
            url=url,
            headers={"Content-Type": "image/png"},
            content=b"png",
        )

    client = FakeImageClient(responder)

    result = await fetch_image_block(
        "https://example.com/start.png", cast(httpx.AsyncClient, client)
    )

    assert result == {"base64": "cG5n", "mime_type": "image/png"}
    assert len(client.calls) == 2
    assert all("Authorization" not in call["headers"] for call in client.calls)


async def test_fetch_image_block_keeps_auth_within_slack_host_family(monkeypatch: Any) -> None:
    _patch_image_dns(monkeypatch)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(multimodal, "create_image_block", lambda **kwargs: kwargs)

    def responder(method: str, url: str, **kwargs: Any) -> FakeImageResponse:
        if kwargs["headers"]["Host"] == "files.slack.com":
            return FakeImageResponse(
                status_code=302,
                url=url,
                headers={"Location": "https://private.files.slack.com/image.png"},
            )
        return FakeImageResponse(
            status_code=200,
            url=url,
            headers={"Content-Type": "image/png"},
            content=b"png",
        )

    client = FakeImageClient(responder)

    result = await fetch_image_block(
        "https://files.slack.com/image.png", cast(httpx.AsyncClient, client)
    )

    assert result == {"base64": "cG5n", "mime_type": "image/png"}
    assert len(client.calls) == 2
    assert all(
        call["headers"]["Authorization"] == "Bearer test-slack-token" for call in client.calls
    )
