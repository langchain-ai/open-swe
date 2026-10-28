"""PostHog capture payload and configuration, shared with the Node and Rust runtimes."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from managed_deepagents._http_status import HttpStatusError
from managed_deepagents._resilience import RetryPolicy
from managed_deepagents._telemetry import (
    PosthogTransport,
    TelemetryEvent,
    is_telemetry_disabled,
    is_truthy_env,
    posthog_transport,
    resolve_posthog_config,
)
from managed_deepagents._telemetry.transport.posthog import DEFAULT_POSTHOG_HOST, _default_post

EVENT: TelemetryEvent = {
    "event_id": "22222222-2222-4222-8222-222222222222",
    "event_type": "run_completed",
    "schema_version": "2026-09-15",
    "created_at": "1970-01-01T00:00:01.234Z",
    "distinct_id": "33333333-3333-4333-8333-333333333333",
}


class _CapturingPost:
    def __init__(self, *, fails: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._fails = fails

    def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> None:
        self.calls.append((url, json.loads(body)))
        if self._fails:
            raise RuntimeError("PostHog rejected analytics event: 400")


NO_RETRY = RetryPolicy(
    max_attempts=1, base_delay_seconds=0, max_delay_seconds=0, retry_on=lambda _e: False
)


def _transport(post: _CapturingPost, host: str = DEFAULT_POSTHOG_HOST) -> PosthogTransport:
    return PosthogTransport(
        api_key="public-test-key", host=host, post=post, max_wait_seconds=0.05, retry=NO_RETRY
    )


def test_posts_queued_events_as_one_batch_request() -> None:
    post = _CapturingPost()
    transport = _transport(post)

    transport.send(EVENT)
    transport.send({**EVENT, "event_id": "second"})
    assert transport.idle(timeout=2)

    assert len(post.calls) == 1
    url, body = post.calls[0]
    assert url == "https://us.i.posthog.com/batch/"
    assert body == {
        "api_key": "public-test-key",
        "batch": [
            {
                "uuid": "22222222-2222-4222-8222-222222222222",
                "event": "run_completed",
                "distinct_id": "33333333-3333-4333-8333-333333333333",
                "timestamp": "1970-01-01T00:00:01.234Z",
                "properties": {
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    "event_id": "22222222-2222-4222-8222-222222222222",
                    "event_type": "run_completed",
                    "schema_version": "2026-09-15",
                    "created_at": "1970-01-01T00:00:01.234Z",
                    "distinct_id": "33333333-3333-4333-8333-333333333333",
                },
            },
            {
                "uuid": "second",
                "event": "run_completed",
                "distinct_id": "33333333-3333-4333-8333-333333333333",
                "timestamp": "1970-01-01T00:00:01.234Z",
                "properties": {
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    **EVENT,
                    "event_id": "second",
                },
            },
        ],
    }


def test_normalizes_a_trailing_slash_on_the_host() -> None:
    post = _CapturingPost()
    transport = _transport(post, host="https://example.test/")

    transport.send(EVENT)
    assert transport.idle(timeout=2)

    assert post.calls[0][0] == "https://example.test/batch/"


def test_send_never_raises_when_posthog_rejects_the_batch() -> None:
    post = _CapturingPost(fails=True)
    transport = _transport(post)

    transport.send(EVENT)

    assert transport.idle(timeout=2)
    assert len(post.calls) == 1


def test_timeout_closes_the_stalled_http_request() -> None:
    async def run() -> None:
        disconnected = asyncio.Event()

        async def stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\n")  # Never finish the response headers.
                await writer.drain()
                await reader.read()  # EOF proves the client closed its socket.
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        async with await asyncio.start_server(stall, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            transport = PosthogTransport(
                api_key="public-test-key",
                host=f"http://127.0.0.1:{port}",
                timeout_seconds=0.25,
                max_wait_seconds=0,
                retry=NO_RETRY,
            )
            transport.send(EVENT)
            assert await asyncio.to_thread(transport.idle, 2.0)
            await asyncio.wait_for(disconnected.wait(), timeout=1.0)

    asyncio.run(run())


def test_rejects_an_empty_api_key() -> None:
    with pytest.raises(ValueError, match="ingestion key is required"):
        PosthogTransport(api_key="")


def test_rejects_an_invalid_host() -> None:
    with pytest.raises(ValueError, match="must be a valid URL"):
        PosthogTransport(api_key="public-test-key", host="not a URL")


def test_config_is_on_without_any_configuration() -> None:
    assert resolve_posthog_config({}, "baked-key") == ("baked-key", DEFAULT_POSTHOG_HOST)


def test_runtime_config_overrides_baked_defaults() -> None:
    config = resolve_posthog_config(
        {
            "MDA_POSTHOG_API_KEY": " runtime-key ",
            "MDA_POSTHOG_HOST": " https://example.test ",
        },
        "baked-key",
    )

    assert config == ("runtime-key", "https://example.test")


def test_blank_runtime_key_falls_back_to_the_baked_key() -> None:
    config = resolve_posthog_config({"MDA_POSTHOG_API_KEY": "  "}, "baked-key")

    assert config is not None
    assert config.api_key == "baked-key"


def test_config_is_absent_without_any_api_key() -> None:
    assert resolve_posthog_config({}, "") is None


def test_transport_is_absent_when_the_host_is_malformed() -> None:
    assert posthog_transport({"MDA_POSTHOG_HOST": "not a URL"}) is None


def test_transport_is_built_from_the_baked_key() -> None:
    assert isinstance(posthog_transport({}), PosthogTransport)


def test_blank_zero_and_false_are_off() -> None:
    for value in ["", "   ", "0", "false", "FALSE", " False "]:
        assert not is_truthy_env(value), value


def test_every_other_value_is_on() -> None:
    for value in ["1", "true", "yes", "maybe"]:
        assert is_truthy_env(value), value


def test_either_switch_alone_opts_out() -> None:
    assert is_telemetry_disabled({"MDA_TELEMETRY_DISABLED": "1"})
    assert is_telemetry_disabled({"DO_NOT_TRACK": "1"})
    assert not is_telemetry_disabled({})
    assert not is_telemetry_disabled({"MDA_TELEMETRY_DISABLED": "0"})


# Local receiver for the real HTTP request tests.
def _serve(status: int) -> tuple[str, list[tuple[str, bytes]], HTTPServer]:
    received: list[tuple[str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            received.append((self.headers["content-type"], self.rfile.read(length)))
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            """Keep the test output quiet."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", received, server


def test_default_post_sends_the_body_and_content_type() -> None:
    url, received, server = _serve(200)
    try:
        transport = PosthogTransport(
            api_key="public-test-key", host=url, max_wait_seconds=0, retry=NO_RETRY
        )
        transport.send(EVENT)
        assert transport.idle(timeout=2)
    finally:
        server.shutdown()

    assert len(received) == 1
    content_type, body = received[0]
    transport.close(timeout=1)
    assert content_type == "application/json"
    assert json.loads(body)["batch"][0]["event"] == "run_completed"


def test_reuses_one_client_across_batches_and_closes_it_on_shutdown() -> None:
    url, received, server = _serve(200)
    try:
        transport = PosthogTransport(
            api_key="public-test-key", host=url, max_wait_seconds=0, retry=NO_RETRY
        )
        client = transport._client
        for _ in range(2):
            transport.send(EVENT)
            assert transport.idle(timeout=2)
        transport.close(timeout=1)
    finally:
        server.shutdown()

    assert len(received) == 2
    assert client is not None
    assert client.is_closed


def test_default_post_raises_a_classified_error_on_a_rejected_batch() -> None:
    url, _, server = _serve(429)
    try:
        with httpx.Client() as client, pytest.raises(HttpStatusError) as raised:
            _default_post(client, f"{url}/batch/", {}, b"{}", 1.0)
    finally:
        server.shutdown()

    assert raised.value.status == 429
    assert raised.value.retryable
