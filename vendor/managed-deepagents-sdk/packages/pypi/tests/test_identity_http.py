"""Unit tests for the managed identity HTTP app (connector routes)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount

from managed_deepagents._connector import Connector
from managed_deepagents._identity_http import build_managed_http_app
from managed_deepagents.identity import define_identity


def _test_identity(**overrides: object) -> Any:
    identity = define_identity(auth="backend")
    identity.config.update(cast("Any", overrides))
    return identity


def _call(
    app: Any,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, bytes, dict[str, str]]:
    """Drive a Starlette app through the ASGI interface without httpx."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    start = next(m for m in messages if m["type"] == "http.response.start")
    status = start["status"]
    response_headers = {
        key.decode().lower(): value.decode() for key, value in start.get("headers", [])
    }
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body, response_headers


def test_adds_cors_headers_on_managed_identity_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_PUBLIC_APP_URL", "https://app.example.com")

    class _DemoConnector(Connector):
        kind = "demo"

        def http(self, ctx: Any) -> None:
            async def whoami(_request: Any, identity: Any) -> Any:
                return PlainTextResponse(identity["user"]["id"])

            ctx.router.get("/whoami", whoami)

    app = build_managed_http_app(
        identity=define_identity(auth="backend"),
        connectors=[_DemoConnector()],
    )
    origin = "https://app.example.com"
    _status, _body, headers = _call(
        app,
        "OPTIONS",
        "/connectors/demo/whoami",
        {"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert headers.get("access-control-allow-origin") == origin


def test_mounted_managed_route_cors_uses_root_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_PUBLIC_APP_URL", "https://app.example.com")

    class _DemoConnector(Connector):
        kind = "demo"

        def http(self, ctx: Any) -> None:
            async def whoami(_request: Any, identity: Any) -> Any:
                return PlainTextResponse(identity["user"]["id"])

            ctx.router.get("/whoami", whoami)

    app = Starlette(
        routes=[Mount("/lgp/probe", app=build_managed_http_app(connectors=[_DemoConnector()]))]
    )
    origin = "https://app.example.com"
    preflight_headers = {"Origin": origin, "Access-Control-Request-Method": "GET"}

    status, _body, headers = _call(
        app, "OPTIONS", "/lgp/probe/connectors/demo/whoami", preflight_headers
    )
    assert status == 200
    assert headers.get("access-control-allow-origin") == origin

    for path in ("/lgp/probe/ok", "/lgp/probe-sibling/connectors/demo/whoami"):
        _status, _body, headers = _call(app, "OPTIONS", path, preflight_headers)
        assert "access-control-allow-origin" not in headers


def test_secured_connector_route_enforces_identity_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_INGRESS_SECRET", "ingress-secret")

    class _DemoConnector(Connector):
        kind = "demo"

        def http(self, ctx: Any) -> None:
            async def whoami(_request: Any, identity: Any) -> Any:
                return PlainTextResponse(identity["user"]["id"])

            ctx.router.get("/whoami", whoami)

    app = build_managed_http_app(
        identity=define_identity(auth="backend"),
        connectors=[_DemoConnector()],
    )

    # Unauthenticated: default-secure → 401, handler never runs.
    status, _body, _headers = _call(app, "GET", "/connectors/demo/whoami")
    assert status == 401

    # Authenticated trusted-backend request: handler receives the identity.
    status, body, _headers = _call(
        app,
        "GET",
        "/connectors/demo/whoami",
        headers={"x-mda-ingress-secret": "ingress-secret", "x-mda-user-id": "user-1"},
    )
    assert status == 200
    assert body == b"user-1"


def test_public_connector_route_needs_no_identity() -> None:
    class _DemoConnector(Connector):
        kind = "demo"

        def http(self, ctx: Any) -> None:
            async def webhook(_request: Any) -> Any:
                return PlainTextResponse("ok")

            ctx.router.public.post("/webhook", webhook)

    app = build_managed_http_app(connectors=[_DemoConnector()])
    status, body, _headers = _call(app, "POST", "/connectors/demo/webhook")
    assert status == 200
    assert body == b"ok"


def test_secured_route_fails_closed_without_identity_declaration() -> None:
    class _DemoConnector(Connector):
        kind = "demo"

        def http(self, ctx: Any) -> None:
            async def whoami(_request: Any, identity: Any) -> Any:
                return PlainTextResponse(identity["user"]["id"])

            ctx.router.get("/whoami", whoami)

    app = build_managed_http_app(connectors=[_DemoConnector()])
    status, _body, _headers = _call(app, "GET", "/connectors/demo/whoami")
    assert status == 401


def test_limits_channel_events_body_before_invoking_channel() -> None:
    class _EventsChannel:
        __mda_channel__ = True
        kind = "test"
        name = "test"
        called = False

        async def events(self, _ctx: object) -> PlainTextResponse:
            self.called = True
            return PlainTextResponse("ok")

    channel = _EventsChannel()
    app = build_managed_http_app(channels=[channel])

    status, body, _headers = _call(
        app,
        "POST",
        "/channels/test/events",
        body=b"x" * (1024 * 1024 + 1),
    )

    assert status == 413
    assert json.loads(body) == {"error": "payload too large"}
    assert channel.called is False

    status, body, _headers = _call(
        app,
        "POST",
        "/channels/test/events",
        body=b"x" * (1024 * 1024),
    )
    assert status == 200
    assert body == b"ok"
    assert channel.called is True


def test_limits_connector_events_body_before_invoking_connector() -> None:
    class _EventsConnector(Connector):
        kind = "github"
        called = False

        async def events(self, _ctx: object) -> PlainTextResponse:
            self.called = True
            return PlainTextResponse("ok")

    connector = _EventsConnector()
    app = build_managed_http_app(connectors=[connector])

    status, body, _headers = _call(
        app,
        "POST",
        "/connectors/github/events",
        body=b"x" * (1024 * 1024 + 1),
    )

    assert status == 413
    assert json.loads(body) == {"error": "payload too large"}
    assert connector.called is False

    status, body, _headers = _call(
        app,
        "POST",
        "/connectors/github/events",
        body=b"x" * (1024 * 1024),
    )
    assert status == 200
    assert body == b"ok"
    assert connector.called is True


def test_explicit_trigger_transport_mounts_non_slack_channel_only_at_v1_route() -> None:
    class _TriggerEventsChannel:
        __mda_channel__ = True
        kind = "discord"
        transport = "trigger_server"
        name = "community"
        called = False

        async def events(self, _ctx: object) -> PlainTextResponse:
            self.called = True
            return PlainTextResponse("ok")

    channel = _TriggerEventsChannel()
    app = build_managed_http_app(channels=[channel])

    status, _body, _headers = _call(app, "POST", "/channels/community/events")
    assert status == 404

    status, body, _headers = _call(
        app,
        "POST",
        "/v1/channels/community/events",
        body=b"x" * (1024 * 1024 + 1),
    )
    assert status == 413
    assert json.loads(body) == {"error": "payload too large"}
    assert channel.called is False

    status, body, _headers = _call(app, "POST", "/v1/channels/community/events", body=b"{}")
    assert status == 200
    assert body == b"ok"
    assert channel.called is True


def test_trigger_route_honors_agent_server_mount_prefix() -> None:
    class _TriggerEventsChannel:
        __mda_channel__ = True
        kind = "discord"
        transport = "trigger_server"
        name = "community"
        called = False

        async def events(self, _ctx: object) -> PlainTextResponse:
            self.called = True
            return PlainTextResponse("ok")

    channel = _TriggerEventsChannel()
    # Python's router strips ASGI root_path natively; JS needs explicit routes.
    app = Starlette(routes=[Mount("/lgp/probe", app=build_managed_http_app(channels=[channel]))])
    status, body, _headers = _call(
        app, "POST", "/lgp/probe/v1/channels/community/events", body=b"{}"
    )
    assert status == 200
    assert body == b"ok"
    assert channel.called is True
    channel.called = False
    for prefix in ("", "/lgp/probe-sibling", "/other"):
        status, _body, _headers = _call(
            app, "POST", f"{prefix}/v1/channels/community/events", body=b"{}"
        )
        assert status == 404
    assert channel.called is False


def test_trigger_looking_kind_without_transport_mounts_only_at_direct_route() -> None:
    class _DirectEventsChannel:
        __mda_channel__ = True
        kind = "trigger_slack"
        name = "slack"

        async def events(self, _ctx: object) -> PlainTextResponse:
            return PlainTextResponse("ok")

    app = build_managed_http_app(channels=[_DirectEventsChannel()])

    status, body, _headers = _call(app, "POST", "/channels/slack/events")
    assert status == 200
    assert body == b"ok"

    status, _body, _headers = _call(app, "POST", "/v1/channels/slack/events")
    assert status == 404


def test_unhandled_channel_events_failure_is_logged_without_leaking_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _ThrowingChannel:
        __mda_channel__ = True
        kind = "test"
        transport = "trigger_server"
        name = "slack"

        async def events(self, _ctx: object) -> PlainTextResponse:
            raise RuntimeError("internal agent details")

    app = build_managed_http_app(channels=[_ThrowingChannel()])
    with caplog.at_level("ERROR", logger="mda"):
        status, body, _headers = _call(app, "POST", "/v1/channels/slack/events", body=b"{}")

    assert status == 500
    assert json.loads(body) == {"error": "internal error"}
    assert "POST /v1/channels/slack/events failed (RuntimeError)" in caplog.text
    assert "internal agent details" not in caplog.text


def test_unhandled_connector_events_failure_is_logged_without_leaking_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _ThrowingConnector(Connector):
        kind = "github"

        async def events(self, _ctx: object) -> PlainTextResponse:
            raise RuntimeError("internal webhook details")

    app = build_managed_http_app(connectors=[_ThrowingConnector()])
    with caplog.at_level("ERROR", logger="mda"):
        status, body, _headers = _call(app, "POST", "/connectors/github/events", body=b"{}")

    assert status == 500
    assert json.loads(body) == {"error": "internal error"}
    assert "POST /connectors/github/events failed (RuntimeError)" in caplog.text
    assert "internal webhook details" not in caplog.text
