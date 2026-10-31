"""Event endpoints: an ``on_event`` value that asks a service what the event starts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, get_args

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from managed_deepagents import GitHubEndpointRequest, LinearEndpointRequest, channels
from managed_deepagents._channels.github_schema import SUPPORTED_EVENT_KEYS as GITHUB_EVENT_KEYS
from managed_deepagents._channels.http import endpoint as endpoint_module
from managed_deepagents._channels.http.endpoint import (
    EndpointDeclined,
    endpoint_handler,
    signature_headers,
    signing_key,
)
from managed_deepagents._channels.http.provider import resolve_endpoints
from managed_deepagents._channels.linear_schema import SUPPORTED_EVENT_KEYS as LINEAR_EVENT_KEYS
from managed_deepagents._channels.run_input import normalize_run_input
from tests.test_channels_linear import payload as linear_payload

_URL = "https://backend.example/runs"


@dataclass(frozen=True)
class _Name:
    name: str = "mylinear"


@dataclass(frozen=True)
class _Event:
    key: str = "AgentSessionEvent.created"
    delivery_id: str = "delivery-1"
    payload: dict[str, Any] = field(default_factory=lambda: {"agentSession": {"id": "s-1"}})
    # Fields the channel derives are not sent; the payload carries them.
    session_id: str = "s-1"


@dataclass(frozen=True)
class _Thread:
    id: str = "thread-1"
    channel: _Name = field(default_factory=_Name)
    interrupted: bool = False


def _service(monkeypatch: pytest.MonkeyPatch, answer: httpx.Response) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(endpoint_module.httpx, "AsyncClient", client)
    return seen


def test_asks_the_service_and_starts_the_run_it_answers_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _service(
        monkeypatch,
        httpx.Response(
            200,
            json={
                "input": {"messages": [{"role": "user", "content": "Fix it"}]},
                "context": {"token": "signed"},
                "metadata": {"ticket": "T-1"},
                "webhook": "https://backend.example/done",
            },
        ),
    )
    # A request carries the headers; MDA sends a POST whatever its method.
    handler = endpoint_handler(httpx.Request("GET", _URL, headers={"x-api-key": "secret"}), "test")

    result = asyncio.run(handler(_Event(), _Thread(interrupted=True)))

    normalized = normalize_run_input(result)
    assert normalized is not None
    assert normalized["context"] == {"token": "signed"}
    assert normalized["metadata"] == {"ticket": "T-1"}
    assert normalized.get("webhook") == "https://backend.example/done"
    [request] = seen
    assert (request.method, str(request.url)) == ("POST", _URL)
    assert request.headers["x-api-key"] == "secret"
    assert request.headers["content-type"] == "application/json"
    # The same JSON the TypeScript runtime sends.
    assert json.loads(request.content) == {
        "event": {
            "key": "AgentSessionEvent.created",
            "deliveryId": "delivery-1",
            "payload": {"agentSession": {"id": "s-1"}},
        },
        "thread": {"id": "thread-1", "channel": {"name": "mylinear"}, "interrupted": True},
    }


def test_takes_the_endpoint_as_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _service(monkeypatch, httpx.Response(200, json={"input": "Triage it"}))
    normalized = normalize_run_input(
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))
    )
    assert normalized is not None
    assert normalized["messages"][0]["content"] == "Triage it"
    assert str(seen[0].url) == _URL


@pytest.mark.parametrize(
    "run_input",
    [[{"role": "user", "content": "hi"}], ["x", {"context": {"token": "t"}}]],
    ids=["messages", "handler-tuple"],
)
def test_refuses_an_array_input(monkeypatch: pytest.MonkeyPatch, run_input: object) -> None:
    _service(monkeypatch, httpx.Response(200, json={"input": run_input}))
    with pytest.raises(RuntimeError, match=r"must be null, \{ decline \} or"):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))


def test_declines_with_the_message_the_service_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    _service(monkeypatch, httpx.Response(200, json={"decline": "Which repository?"}))
    result = asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))
    assert result == EndpointDeclined("Which repository?")


@pytest.mark.parametrize(
    "answer",
    [
        {"decline": ""},
        {"decline": "  "},
        {"decline": "x" * 2001},
        {"decline": 1},
        {"decline": "why", "input": "x"},
    ],
    ids=["empty", "blank", "too-long", "not-text", "with-input"],
)
def test_fails_the_handler_on_a_decline_it_cant_post(
    monkeypatch: pytest.MonkeyPatch, answer: dict[str, object]
) -> None:
    _service(monkeypatch, httpx.Response(200, json=answer))
    with pytest.raises(RuntimeError, match=r"must be null, \{ decline \} or"):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))


def _answers(
    monkeypatch: pytest.MonkeyPatch, answers: list[httpx.Response | Exception]
) -> list[httpx.Request]:
    """A service that answers each attempt in turn; the last answer repeats."""
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = answers[min(len(seen), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(
        endpoint_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(endpoint_module, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    return seen


def test_tries_a_network_error_429_or_5xx_again_with_the_same_webhook_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SPEC_SECRET)
    seen = _answers(
        monkeypatch,
        [
            httpx.ConnectError("refused"),
            httpx.Response(503, text="busy"),
            httpx.Response(200, json={"input": "Triage it"}),
        ],
    )
    result = asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))
    assert normalize_run_input(result) is not None
    assert [request.headers["webhook-id"] for request in seen] == ["msg_delivery-1"] * 3


def test_fails_the_handler_after_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _answers(monkeypatch, [httpx.Response(429, text="slow down")])
    with pytest.raises(RuntimeError, match="HTTP 429"):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))
    assert len(seen) == 3


def test_doesnt_try_a_timed_out_request_again(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _answers(monkeypatch, [httpx.ReadTimeout("slow")])
    with pytest.raises(RuntimeError, match="did not answer within"):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))
    assert len(seen) == 1


class _CutOff(httpx.AsyncByteStream):
    """An answer whose connection drops partway through the body."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"input": "Tri'
        raise httpx.ReadError("connection dropped")


def test_doesnt_try_again_when_an_answer_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # The service took the request, so asking again could repeat what it did.
    seen = _answers(monkeypatch, [httpx.Response(200, stream=_CutOff())])
    with pytest.raises(RuntimeError, match="cut off"):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))
    assert len(seen) == 1


def test_names_the_interrupted_call_apart_from_the_one_that_starts_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SPEC_SECRET)
    seen = _answers(monkeypatch, [httpx.Response(204)])
    handler = endpoint_handler(_URL, "test")
    asyncio.run(handler(_Event(), _Thread()))
    asyncio.run(handler(_Event(), _Thread(interrupted=True)))
    assert [request.headers["webhook-id"] for request in seen] == [
        "msg_delivery-1",
        "msg_delivery-1_interrupted",
    ]


@pytest.mark.parametrize(
    "answer", [httpx.Response(200, content=b"null"), httpx.Response(204)], ids=["null", "204"]
)
def test_starts_nothing_when_the_service_says_so(
    monkeypatch: pytest.MonkeyPatch, answer: httpx.Response
) -> None:
    _service(monkeypatch, answer)
    assert asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread())) is None


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (httpx.Response(400, text="nope"), "HTTP 400"),
        (httpx.Response(302, headers={"location": "http://169.254.169.254/"}), "HTTP 302"),
        (httpx.Response(200, text="not json"), "not JSON"),
        (httpx.Response(200, json={"context": {}}), "must be null, { decline } or"),
        (httpx.Response(200, json={"input": "x", "config": {}}), "must be null, { decline } or"),
        (
            httpx.Response(200, headers={"content-length": str(11 * 1024 * 1024)}, text="x"),
            "too large",
        ),
    ],
    ids=["error", "redirect", "text", "no-input", "unknown-option", "oversized"],
)
def test_fails_the_handler_on_an_answer_it_cant_use(
    monkeypatch: pytest.MonkeyPatch, answer: httpx.Response, message: str
) -> None:
    _service(monkeypatch, answer)
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))


def test_fails_the_handler_when_the_whole_answer_takes_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def trickle() -> Any:
        # Each chunk arrives well within a read timeout; the answer as a whole doesn't.
        yield b'{"input": '
        for _ in range(20):
            await asyncio.sleep(0.01)
            yield b" "
        yield b'"late"}'

    _service(monkeypatch, httpx.Response(200, content=trickle()))
    monkeypatch.setattr(endpoint_module, "_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(RuntimeError, match="did not answer within"):
        asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))


def test_resolves_endpoints_and_keeps_handler_functions() -> None:
    def local(_event: object, _thread: object) -> str:
        return "prompt"

    resolved = resolve_endpoints("GitHub", {"issues.opened": local, "issues.edited": _URL})
    assert resolved["issues.opened"] is local
    assert callable(resolved["issues.edited"])


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/runs",
        "http://api.localhost/runs",
        "http://127.0.0.1:8080/runs",
        "http://[::1]:8080/runs",
    ],
)
def test_takes_plain_http_to_a_loopback_host(url: str) -> None:
    channels.github(on_event={"issues.opened": url})


@pytest.mark.parametrize(
    "url",
    [
        "/runs",
        "ftp://backend.example/runs",
        "http://backend.example/runs",
        "http://10.0.0.1/runs",
        "https://user:pass@backend.example/runs",
    ],
)
def test_refuses_an_endpoint_it_cant_use_when_the_channel_is_declared(url: str) -> None:
    with pytest.raises(
        TypeError,
        match=r'GitHub on_event\["issues.opened"\] must be an absolute https URL, or http to',
    ):
        channels.github(on_event={"issues.opened": url})
    with pytest.raises(TypeError):
        channels.linear(on_event={"AgentSessionEvent.created": httpx.Request("POST", url)})


@pytest.mark.parametrize(
    ("provider", "declare"),
    [
        ("GitHub", lambda: channels.github(on_event={"issues.opened": 42})),
        ("Linear", lambda: channels.linear(on_event={"Issue.create": 42})),
        ("Slack", lambda: channels.slack(on_event={"mention": 42})),
    ],
)
def test_refuses_a_handler_that_is_neither_a_function_nor_an_endpoint(
    provider: str, declare: Callable[[], object]
) -> None:
    with pytest.raises(TypeError, match=rf'{provider} on_event\["'):
        declare()


def test_takes_an_endpoint_for_any_github_or_linear_event() -> None:
    backend = httpx.Request("POST", _URL, headers={"x-api-key": "secret"})
    github = channels.github(on_event={"issues.opened": backend, "issue_comment.created": _URL})
    # Deploy subscribes the app to endpoint events too.
    assert github.requirements("github")["providerConfig"]["events"] == [
        "issue_comment.created",
        "issues.opened",
    ]
    channels.linear(on_event={"AgentSessionEvent.created": backend, "Issue.update": _URL})


# The Standard Webhooks spec's own example.
_SPEC_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
_OTHER_SECRET = "whsec_" + base64.b64encode(bytes([7]) * 32).decode()


def _verifies(secret: str, request: httpx.Request) -> bool:
    """Verify as a Standard Webhooks receiver does."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = (
        f"{request.headers['webhook-id']}.{request.headers['webhook-timestamp']}.".encode()
        + request.content
    )
    expected = hmac.digest(key, signed, hashlib.sha256)
    return any(
        version == "v1" and hmac.compare_digest(base64.b64decode(signature), expected)
        for version, _, signature in (
            entry.partition(",") for entry in request.headers["webhook-signature"].split(" ")
        )
    )


def test_signs_as_the_standard_webhooks_example_does() -> None:
    assert signature_headers(
        [signing_key(_SPEC_SECRET)],
        "msg_p5jXN8AQM9LWM0D4loKWxJek",
        1614265330,
        b'{"test": 2432232314}',
    ) == {
        "webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek",
        "webhook-timestamp": "1614265330",
        "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
    }


def test_signs_the_exact_body_it_sends_with_every_secret_the_deployment_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stray separators, as rotation leaves behind, are ignored.
    monkeypatch.setenv("MDA_SERVICE_SECRET", f" ,{_SPEC_SECRET}, {_OTHER_SECRET}, ")
    seen = _service(monkeypatch, httpx.Response(204))
    monkeypatch.setattr(endpoint_module.time, "time", lambda: 1_760_900_000.5)
    handler = endpoint_handler(
        httpx.Request("POST", _URL, headers={"X-Api-Key": "key", "Webhook-Id": "forged"}),
        "test",
    )

    asyncio.run(handler(_Event(), _Thread()))

    [request] = seen
    assert str(request.url) == _URL
    assert request.headers["x-api-key"] == "key"
    assert request.headers["webhook-id"] == "msg_delivery-1"
    assert request.headers["webhook-timestamp"] == "1760900000"
    assert len(request.headers["webhook-signature"].split(" ")) == 2
    assert _verifies(_SPEC_SECRET, request)
    assert _verifies(_OTHER_SECRET, request)
    tampered = httpx.Request(
        "POST",
        _URL,
        headers=request.headers,
        content=request.content.replace(b"delivery-1", b"delivery-2"),
    )
    assert not _verifies(_SPEC_SECRET, tampered)


@pytest.mark.parametrize("secret", [None, "", "  ", " , ,"])
def test_sends_no_signature_when_the_deployment_has_no_secret(
    monkeypatch: pytest.MonkeyPatch, secret: str | None
) -> None:
    if secret is None:
        monkeypatch.delenv("MDA_SERVICE_SECRET", raising=False)
    else:
        monkeypatch.setenv("MDA_SERVICE_SECRET", secret)
    seen = _service(monkeypatch, httpx.Response(204))

    asyncio.run(endpoint_handler(_URL, "test")(_Event(), _Thread()))

    assert "webhook-signature" not in seen[0].headers
    assert "webhook-id" not in seen[0].headers


def test_raises_when_declared_without_echoing_a_malformed_deployment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "whsec_tooshort"
    monkeypatch.setenv("MDA_SERVICE_SECRET", secret)
    seen = _service(monkeypatch, httpx.Response(204))

    with pytest.raises(TypeError, match="MDA_SERVICE_SECRET") as raised:
        endpoint_handler(_URL, "test")

    assert secret not in str(raised.value)
    assert seen == []


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "whsec_not-base64!",
        "whsec_" + base64.b64encode(bytes([7]) * 32).decode().rstrip("="),
        "whsec_" + base64.b64encode(bytes(16)).decode(),
        "whsec_" + base64.b64encode(bytes(65)).decode(),
    ],
    ids=["empty", "not-base64", "unpadded", "too-short", "too-long"],
)
def test_refuses_a_malformed_secret_without_echoing_it(secret: str) -> None:
    with pytest.raises(TypeError, match="Standard Webhooks secrets") as raised:
        signing_key(secret)

    if secret:
        assert secret not in str(raised.value)


def test_accepts_a_secret_without_the_whsec_prefix() -> None:
    assert signing_key(_SPEC_SECRET.removeprefix("whsec_")) == signing_key(_SPEC_SECRET)


def test_accepts_a_hex_secret_as_openssl_rand_hex_makes_it() -> None:
    assert len(signing_key("ab" * 32)) == 48


def _union_keys(union: object) -> set[str]:
    """The event keys a union of ``EndpointRequest[EndpointEvent[Literal[...], P]]`` names."""
    keys: set[str] = set()
    for request in get_args(union):
        (event,) = get_args(request)
        key, _payload = get_args(event)
        keys.update(get_args(key))
    return keys


@pytest.mark.parametrize(
    ("union", "supported"),
    [
        (GitHubEndpointRequest, GITHUB_EVENT_KEYS),
        (LinearEndpointRequest, LINEAR_EVENT_KEYS),
    ],
    ids=["github", "linear"],
)
def test_a_channels_endpoint_request_covers_every_key_it_sends(
    union: object, supported: frozenset[str]
) -> None:
    # A key the channel supports but the union misses would fail receivers.
    assert _union_keys(union) == supported


@pytest.mark.parametrize("key", sorted(LINEAR_EVENT_KEYS))
def test_a_linear_endpoint_request_takes_each_keys_payload(key: str) -> None:
    requests = TypeAdapter(LinearEndpointRequest)
    thread = {"id": "t-1", "channel": {"name": "linear"}}
    request = requests.validate_python(
        {
            "event": {"key": key, "deliveryId": "d-1", "payload": linear_payload(key)},
            "thread": thread,
        }
    )
    assert request["event"]["key"] == key


@pytest.mark.parametrize("request_type", [GitHubEndpointRequest, LinearEndpointRequest])
def test_an_endpoint_request_refuses_a_key_its_channel_doesnt_send(request_type: object) -> None:
    # The key picks the payload's type, so an unknown key matches no member.
    with pytest.raises(ValidationError):
        TypeAdapter(request_type).validate_python(
            {
                "event": {"key": "not-a-key", "deliveryId": "d-1", "payload": {}},
                "thread": {"id": "t-1", "channel": {"name": "c"}},
            }
        )
