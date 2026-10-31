"""A channel's verified sender, mapped to one of the app's users by ``resolve_user``."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from managed_deepagents import SlackActor, channels, define_identity
from managed_deepagents._agent_auth_http import ConnectionsNotConfiguredError
from managed_deepagents._channel import collect_channels
from managed_deepagents._channels import resolve_user as resolve_user_module
from managed_deepagents._channels.actor import (
    ChannelActor,
    GitHubActor,
    LinearActor,
    actor_identity,
)
from managed_deepagents._channels.http import endpoint as endpoint_module
from managed_deepagents._channels.resolve_user import (
    ResolveUserEndpoint,
    ResolveUserResult,
    checked_resolve_user,
    resolve_channel_user,
)
from managed_deepagents._channels.trigger import http as trigger_http
from managed_deepagents._channels.trigger.http import TriggerMessageDecision
from managed_deepagents._identity_http import build_managed_http_app
from tests.test_channels_endpoint import _SPEC_SECRET, _verifies
from tests.test_channels_http import ingress, make_channel, message  # noqa: F401 - fixture
from tests.test_channels_slack_actor import _source_thread, _token
from tests.test_identity_http import _call
from tests.test_trigger_message_smoke import (
    _IDENTITY_ISSUER,
    _IDENTITY_KEY,
    _IDENTITY_PRINCIPAL_ID,
)

_ACTOR = SlackActor(id="U1", team_id="T1")
_MAPPED = "55555555-5555-4555-8555-555555555555"


def _resolve(result: ResolveUserResult) -> tuple[Any, list[object]]:
    resolved: list[object] = []

    def resolve_principal(identity: object) -> str:
        resolved.append(identity)
        return "principal-1"

    user = asyncio.run(
        resolve_channel_user(
            lambda _request: result, _ACTOR, "support", "delivery-1", resolve_principal
        )
    )
    return user, resolved


def test_runs_one_of_the_apps_users_as_the_principal_a_backend_call_gets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "workspace-1")

    user, resolved = _resolve({"type": "user", "id": "usr_123"})

    assert user == {"type": "principal", "id": "principal-1"}
    assert resolved == [
        {"provider": "trusted_backend", "issuer": "workspace-1", "subject": "usr_123"}
    ]


def test_runs_an_exact_identity_as_its_principal() -> None:
    user, resolved = _resolve(
        {"type": "identity", "provider": "supabase", "issuer": "iss", "subject": "sub-1"}
    )

    assert user == {"type": "principal", "id": "principal-1"}
    assert resolved == [{"provider": "supabase", "issuer": "iss", "subject": "sub-1"}]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {"type": "reply", "message": {"type": "content", "content": "Link first."}},
            {"type": "reply", "message": {"type": "content", "content": "Link first."}},
        ),
        (
            {"type": "reply", "message": {"type": "native", "native": {"blocks": []}}},
            {"type": "reply", "message": {"type": "native", "native": {"blocks": []}}},
        ),
        ({"type": "ignore"}, {"type": "ignore"}),
    ],
)
def test_passes_reply_and_ignore_on_without_a_principal(
    result: ResolveUserResult, expected: object
) -> None:
    assert _resolve(result) == (expected, [])


def test_keeps_the_channels_principal_for_none_or_no_hook() -> None:
    assert _resolve(None) == (None, [])
    assert asyncio.run(resolve_channel_user(None, _ACTOR, "support", "delivery-1")) is None


def test_asks_without_an_actor_when_the_channel_verified_none() -> None:
    requests: list[object] = []

    def resolve_user(request: object) -> ResolveUserResult:
        requests.append(request)
        return {"type": "ignore"}

    assert asyncio.run(resolve_channel_user(resolve_user, None, "support", "delivery-1")) == {
        "type": "ignore"
    }
    assert requests == [{"actor": None, "channel": {"name": "support"}}]


def test_rejects_a_result_it_does_not_know() -> None:
    with pytest.raises(TypeError, match="resolve_user must return"):
        _resolve({"type": "admin"})  # type: ignore[typeddict-item]


@pytest.mark.parametrize(
    "result",
    [
        {"type": "user"},
        {"type": "user", "id": " "},
        {"type": "user", "id": 42},
        {"type": "identity", "provider": "p", "issuer": "i"},
        {"type": "reply", "message": "hi"},
        {"type": "ignore", "reason": "spam"},
    ],
)
def test_rejects_a_result_with_bad_fields_before_it_is_used(result: Any) -> None:
    with pytest.raises(TypeError, match="resolve_user must return"):
        _resolve(result)


class _Request:
    def __init__(self) -> None:
        self.headers = {"x-service-key": "present"}


class _Client:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict[str, Any]]] = []

    async def post_action(self, channel_id: str, body: dict[str, Any]) -> dict[str, str]:
        self.actions.append((channel_id, body))
        return {"id": "reply-1"}


def _deliver(
    resolve_user: Any, *, run_work: bool = False, **options: Any
) -> tuple[Any, list[dict[str, object]], _Client]:
    started: list[dict[str, object]] = []
    work: list[Any] = []

    async def start(**kwargs: object) -> dict[str, str]:
        started.append(kwargs)
        return {"thread_id": str(kwargs["thread_id"]), "run_id": "run-1"}

    client = _Client()
    envelope = {
        "version": "1",
        "type": "message",
        "event_id": "Ev1",
        "delivery_id": "delivery-1",
        "created_at": "2026-10-26T12:00:00Z",
        "channel_id": "4f431bf2-19ed-45a9-8e00-2d1e7567f64b",
        "source_thread_key": "slack:v1:A1:T1:C1:171.1",
        "address": {"conversation_id": "C1", "thread_id": "171.1"},
        "input": {"messages": [{"type": "human", "content": "hi"}]},
        "identity_context": _token(),
        "actor": {"provider": "slack", "issuer": "T1", "subject": "U1"},
    }
    response = asyncio.run(
        trigger_http.handle_trigger_channel_event_request(
            _Request(),
            raw_body=json.dumps(envelope).encode(),
            source_provider="slack",
            channel_name="support",
            assistant_id="agent",
            reactions=False,
            trigger_client=client,  # type: ignore[arg-type]
            identity_context_key=_IDENTITY_KEY.public_key(),
            identity_context_issuer=_IDENTITY_ISSUER,
            schedule=work.append,
            start_agent=start,  # type: ignore[arg-type]
            conversation_thread=_source_thread,
            resolve_user=resolve_user,
            resolve_principal=lambda identity: (
                _IDENTITY_PRINCIPAL_ID if identity["provider"] == "slack" else _MAPPED
            ),
            **options,
        )
    )
    # Only a delivery that starts no run has work that needs no Agent Server.
    for job in work if run_work else []:
        asyncio.run(job())
    return response, started, client


def test_a_slack_run_acts_as_the_principal_of_the_mapped_user() -> None:
    requests: list[object] = []

    def resolve_user(request: object) -> ResolveUserResult:
        requests.append(request)
        return {"type": "identity", "provider": "supabase", "issuer": "iss", "subject": "sub-1"}

    response, started, _client = _deliver(resolve_user)

    assert response.status_code == 202
    assert requests == [{"actor": _ACTOR, "channel": {"name": "support"}}]
    assert [start["user_id"] for start in started] == [_MAPPED]


def test_a_slack_ignore_starts_nothing() -> None:
    response, started, client = _deliver(lambda _request: {"type": "ignore"})

    assert response.status_code == 202
    assert started == []
    assert client.actions == []


def test_a_slack_reply_is_posted_and_starts_nothing() -> None:
    response, started, client = _deliver(
        lambda _request: {
            "type": "reply",
            "message": {"type": "content", "content": "Link your account first."},
        }
    )

    assert response.status_code == 202
    assert started == []
    [(_channel_id, action)] = client.actions
    assert action["type"] == "post"
    assert action["message"] == {"text": "Link your account first."}


def test_a_slack_reply_only_where_a_run_would_start_and_it_finishes() -> None:
    finished: list[Any] = []

    async def on_finish(_target: object, delivery: Any) -> None:
        finished.append(delivery)

    async def skip(*_args: object) -> TriggerMessageDecision:
        return TriggerMessageDecision("skipped")

    def reply(_request: object) -> ResolveUserResult:
        return {
            "type": "reply",
            "message": {"type": "content", "content": "Link your account first."},
        }

    _response, _started, declined = _deliver(reply, run_work=True, decide_message=skip)
    _response, started, withheld = _deliver(reply, run_work=True, on_finish=on_finish)

    assert declined.actions == []
    assert len(withheld.actions) == 1
    assert started == []
    assert [(d["type"], d["reason"]) for d in finished] == [("skipped", "ignored")]


def test_a_sync_resolve_user_runs_off_the_event_loop() -> None:
    threads: list[str] = []

    def resolve_user(_request: object) -> ResolveUserResult:
        threads.append(threading.current_thread().name)
        return None

    asyncio.run(resolve_channel_user(resolve_user, _ACTOR, "support", "delivery-1"))

    assert threads != [threading.main_thread().name]


def test_a_failing_resolve_user_starts_nothing() -> None:
    def resolve_user(_request: object) -> ResolveUserResult:
        raise RuntimeError("users table is down")

    response, started, _client = _deliver(resolve_user)

    assert response.status_code == 500
    assert started == []


@pytest.fixture
def withheld_start(ingress: SimpleNamespace) -> SimpleNamespace:  # noqa: F811 - fixture
    """As the real start: a withheld message decides its input, then starts nothing."""

    async def start(**kwargs: Any) -> dict[str, str]:
        if kwargs.get("input") is None and await kwargs["prepare"]() is None:
            return {"skipped": "ignored"}
        return {"thread_id": "thread-1", "run_id": "run-1"}

    ingress.start.side_effect = start
    return ingress


def _send(
    *,
    identity: Any = None,
    channel: Any = None,
    actor: ChannelActor | None = None,
    **adapter: Any,
) -> int:
    actor = actor or GitHubActor(id="7", name="octocat", type="User")
    http_channel = make_channel(
        parse=lambda _ctx: {
            "type": "message",
            "message": {**message(), "delivery_key": "delivery-7"},
            "actor": actor,
        },
        **adapter,
    )
    http_channel.resolve_user = channel
    collect_channels([("channels/orders.py", SimpleNamespace(channel=http_channel))])
    app = build_managed_http_app(
        identity=define_identity(auth="backend", resolve_user=identity),
        channels=[http_channel],
    )
    status, _body, _headers = _call(app, "POST", "/channels/orders/events", body=b"{}")
    return status


def _run_jobs(ingress: SimpleNamespace) -> None:  # noqa: F811 - fixture
    while ingress.jobs:
        asyncio.run(ingress.jobs.pop(0)())


def test_an_http_run_carries_the_mapped_principal(
    ingress: SimpleNamespace,  # noqa: F811 - fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "managed_deepagents._channels.resolve_user.resolve_external_principal_id",
        lambda _identity: _MAPPED,
    )

    status = _send(
        identity=lambda _request: {
            "type": "identity",
            "provider": "supabase",
            "issuer": "iss",
            "subject": "sub-1",
        },
    )

    assert status == 202
    assert ingress.start.call_args.kwargs["user_id"] == _MAPPED
    assert ingress.start.call_args.kwargs["principal_id"] == _MAPPED


@pytest.mark.parametrize(
    ("actor", "identity"),
    [
        (
            GitHubActor(id="583231", name="octocat", type="User"),
            {"provider": "github", "issuer": "github.com", "subject": "583231"},
        ),
        (
            LinearActor(id="lin-1", type="user"),
            {"provider": "linear", "issuer": "linear.app", "subject": "lin-1"},
        ),
        # A session's creator comes without a type.
        (
            LinearActor(id="lin-1"),
            {"provider": "linear", "issuer": "linear.app", "subject": "lin-1"},
        ),
        (GitHubActor(id="41898282", name="bot", type="Bot"), None),
        (LinearActor(id="app-1", type="OauthClient"), None),
        # Trigger Server already resolved the Slack sender.
        (_ACTOR, None),
    ],
)
def test_actor_identity(actor: ChannelActor, identity: object) -> None:
    assert actor_identity(actor) == identity


def test_an_unmapped_person_runs_as_their_own_principal_without_user_connections(
    ingress: SimpleNamespace,  # noqa: F811 - fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[object] = []

    def resolve(identity: object) -> str:
        resolved.append(identity)
        return _MAPPED

    monkeypatch.setattr(
        "managed_deepagents._channels.resolve_user.resolve_external_principal_id", resolve
    )

    status = _send(identity=lambda _request: None)

    assert status == 202
    assert resolved == [{"provider": "github", "issuer": "github.com", "subject": "7"}]
    assert ingress.start.call_args.kwargs["user_id"] == _MAPPED
    assert ingress.start.call_args.kwargs["principal_id"] is None


def test_a_bot_runs_as_the_channel(
    ingress: SimpleNamespace,  # noqa: F811 - fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[object] = []
    monkeypatch.setattr(
        "managed_deepagents._channels.resolve_user.resolve_external_principal_id",
        resolved.append,
    )

    _send(actor=GitHubActor(id="8", name="ci", type="Bot"))

    assert resolved == []
    assert ingress.start.call_args.kwargs["user_id"] == message()["user_id"]


def test_without_agent_auth_the_channels_user_id_stays(
    ingress: SimpleNamespace,  # noqa: F811 - fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unconfigured(_identity: object) -> str:
        raise ConnectionsNotConfiguredError("LANGSMITH_API_KEY")

    monkeypatch.setattr(
        "managed_deepagents._channels.resolve_user.resolve_external_principal_id", unconfigured
    )

    assert _send() == 202
    assert ingress.start.call_args.kwargs["user_id"] == message()["user_id"]


def test_a_failing_agent_auth_starts_nothing(
    ingress: SimpleNamespace,  # noqa: F811 - fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_identity: object) -> str:
        raise RuntimeError("Agent Auth unavailable")

    monkeypatch.setattr(
        "managed_deepagents._channels.resolve_user.resolve_external_principal_id", unavailable
    )

    assert _send() == 500
    ingress.start.assert_not_called()


def test_the_channels_resolve_user_comes_before_the_identitys(
    withheld_start: SimpleNamespace,
) -> None:
    asked: list[object] = []

    status = _send(identity=asked.append, channel=lambda _request: {"type": "ignore"})
    _run_jobs(withheld_start)

    assert status == 202
    assert asked == []
    withheld_start.observe.assert_not_called()


def test_an_http_reply_is_acknowledged_finished_and_posted_once_per_delivery(
    withheld_start: SimpleNamespace,
) -> None:
    posts: list[object] = []
    received: list[object] = []
    finished: list[Any] = []

    async def post(message: object) -> dict[str, str]:
        posts.append(message)
        return {"id": "m1"}

    async def on_received(target: object, _delivery: object) -> None:
        received.append(target)

    async def on_finish(_target: object, delivery: Any) -> None:
        finished.append(delivery)

    def reply(_request: object) -> ResolveUserResult:
        return {"type": "reply", "message": {"type": "content", "content": "Link first."}}

    for _ in range(2):
        _send(channel=reply, post=post, on_received=on_received, on_finish=on_finish)
        _run_jobs(withheld_start)

    assert posts == [{"type": "content", "content": "Link first.", "target": message()["target"]}]
    assert len(received) == 2
    assert {(d["type"], d["reason"]) for d in finished} == {("skipped", "ignored")}
    withheld_start.observe.assert_not_called()


# An endpoint: MDA asks a service of yours instead of calling a function.

_URL = "https://backend.example/resolve-user"


def _service(
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

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(endpoint_module.httpx, "AsyncClient", client)
    monkeypatch.setattr(resolve_user_module, "_ENDPOINT_RETRY_DELAYS_SECONDS", (0.0,))
    return seen


def _ask(endpoint: Any = _URL, actor: Any = _ACTOR) -> Any:
    return asyncio.run(
        resolve_channel_user(
            checked_resolve_user("channels.slack", endpoint),
            actor,
            "support",
            "delivery-1",
            lambda _identity: "principal-1",
        )
    )


def test_an_endpoint_is_sent_the_actor_signed_with_the_endpoints_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SPEC_SECRET)
    seen = _service(
        monkeypatch,
        [
            httpx.Response(
                200, json={"type": "identity", "provider": "p", "issuer": "i", "subject": "s"}
            )
        ],
    )

    user = _ask(httpx.Request("GET", _URL, headers={"x-api-key": "secret"}))

    assert user == {"type": "principal", "id": "principal-1"}
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == _URL
    assert request.headers["x-api-key"] == "secret"
    assert request.headers["webhook-id"] == "msg_delivery-1_resolve_user"
    assert _verifies(_SPEC_SECRET, request)
    # The actor in camelCase, as TypeScript's runtime sends it.
    assert json.loads(request.content) == {
        "actor": {"provider": "slack", "id": "U1", "teamId": "T1"},
        "channel": {"name": "support"},
    }


def test_an_endpoint_is_sent_no_actor_when_the_channel_verified_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _service(monkeypatch, [httpx.Response(200, json={"type": "ignore"})])

    assert _ask(actor=None) == {"type": "ignore"}
    assert json.loads(seen[0].content) == {"channel": {"name": "support"}}


@pytest.mark.parametrize("answer", [httpx.Response(204), httpx.Response(200, content=b"null")])
def test_an_endpoint_keeps_the_channels_principal_for_204_or_null(
    monkeypatch: pytest.MonkeyPatch, answer: httpx.Response
) -> None:
    _service(monkeypatch, [answer])

    assert _ask() is None


@pytest.mark.parametrize(
    ("answer", "error", "message"),
    [
        (httpx.Response(200, json={"type": "admin"}), TypeError, "resolve_user must return"),
        (httpx.Response(200, json={}), TypeError, "resolve_user must return"),
        (httpx.Response(200, json=["user"]), TypeError, "resolve_user must return"),
        (
            httpx.Response(200, json={"type": "user", "id": 42}),
            TypeError,
            "resolve_user must return",
        ),
        (
            httpx.Response(200, json={"type": "reply", "message": "hi"}),
            TypeError,
            "resolve_user must return",
        ),
        (httpx.Response(200, text="not json"), RuntimeError, "not JSON"),
        (httpx.Response(400, text="nope"), RuntimeError, "HTTP 400"),
        (
            httpx.Response(302, headers={"location": "http://169.254.169.254/"}),
            RuntimeError,
            "HTTP 302",
        ),
    ],
)
def test_an_endpoints_bad_answer_fails_as_a_raising_function_does(
    monkeypatch: pytest.MonkeyPatch, answer: httpx.Response, error: type[Exception], message: str
) -> None:
    seen = _service(monkeypatch, [answer])

    with pytest.raises(error, match=message):
        _ask()
    assert len(seen) == 1


def test_an_endpoint_is_tried_once_more_with_the_same_webhook_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SPEC_SECRET)
    seen = _service(
        monkeypatch, [httpx.ConnectError("refused"), httpx.Response(200, json={"type": "ignore"})]
    )

    assert _ask() == {"type": "ignore"}
    assert [r.headers["webhook-id"] for r in seen] == ["msg_delivery-1_resolve_user"] * 2


def test_an_endpoint_fails_after_two_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _service(monkeypatch, [httpx.Response(503, text="busy")])

    with pytest.raises(RuntimeError, match="HTTP 503"):
        _ask()
    assert len(seen) == 2


def test_an_endpoints_attempts_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _service(monkeypatch, [httpx.ConnectError("refused")])
    monkeypatch.setattr(resolve_user_module, "_ENDPOINT_RETRY_DELAYS_SECONDS", (1.0,))
    monkeypatch.setattr(resolve_user_module, "_ENDPOINT_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(RuntimeError, match="resolve_user endpoint did not answer within"):
        _ask()
    # The deadline came during the pause, before the second attempt.
    assert len(seen) == 1


def test_an_endpoint_that_times_out_is_not_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _service(monkeypatch, [httpx.ReadTimeout("slow")])

    with pytest.raises(RuntimeError, match="did not answer within 5 seconds"):
        _ask()
    assert len(seen) == 1


def test_a_failing_endpoint_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _service(monkeypatch, [httpx.Response(500, text="users table is down")])

    response, started, _client = _deliver(checked_resolve_user("channels.slack", _URL))

    assert response.status_code == 500
    assert started == []


def test_a_slack_run_acts_as_the_principal_the_endpoint_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _service(
        monkeypatch,
        [
            httpx.Response(
                200, json={"type": "identity", "provider": "p", "issuer": "i", "subject": "s"}
            )
        ],
    )

    response, started, _client = _deliver(checked_resolve_user("channels.slack", _URL))

    assert response.status_code == 202
    assert [start["user_id"] for start in started] == [_MAPPED]
    assert json.loads(seen[0].content)["actor"] == {"provider": "slack", "id": "U1", "teamId": "T1"}


@pytest.mark.parametrize(
    "url",
    [
        "/resolve-user",
        "http://backend.example/resolve-user",
        "https://user:pass@backend.example/resolve-user",
    ],
)
def test_refuses_an_endpoint_it_cant_use_when_declared(url: str) -> None:
    with pytest.raises(
        TypeError, match=r"define_identity\(\.\.\.\) resolve_user must be an absolute"
    ):
        define_identity(auth="backend", resolve_user=url)
    with pytest.raises(TypeError, match=r"channels\.slack\(\.\.\.\) resolve_user must be"):
        channels.slack(resolve_user=url)
    with pytest.raises(TypeError, match=r"channels\.github\(\.\.\.\) resolve_user must be"):
        channels.github(resolve_user=httpx.Request("POST", url))
    with pytest.raises(TypeError, match=r"channels\.linear\(\.\.\.\) resolve_user must be"):
        channels.linear(resolve_user=url)


def test_refuses_a_malformed_secret_when_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", "whsec_tooshort")

    with pytest.raises(TypeError, match="MDA_SERVICE_SECRET") as raised:
        define_identity(auth="backend", resolve_user=_URL)
    assert "tooshort" not in str(raised.value)
    with pytest.raises(TypeError, match="MDA_SERVICE_SECRET"):
        channels.slack(resolve_user=_URL)


def test_takes_an_endpoint_on_the_identity_and_every_channel() -> None:
    backend = httpx.Request("POST", _URL, headers={"x-api-key": "secret"})

    assert isinstance(
        define_identity(auth="backend", resolve_user=_URL).resolve_user, ResolveUserEndpoint
    )
    for channel in (
        channels.slack(resolve_user=backend),
        channels.github(resolve_user=_URL),
        channels.linear(resolve_user="http://localhost:8080/resolve-user"),
    ):
        assert isinstance(channel.resolve_user, ResolveUserEndpoint)


def test_user_resolution_names_how_ingress_chose_the_runs_user() -> None:
    from managed_deepagents._channels.resolve_user import user_resolution  # noqa: PLC0415

    assert user_resolution({"type": "principal", "id": "p-1"}, ran_as_sender=True) == (
        "application_resolved"
    )
    assert user_resolution(None, ran_as_sender=True) == "agent_auth_principal"
    assert user_resolution(None, ran_as_sender=False) == "channel"
