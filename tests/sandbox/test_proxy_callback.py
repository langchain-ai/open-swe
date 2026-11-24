"""Who the sandbox proxy callback answers, and with what."""

import base64
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from jwt.algorithms import OKPAlgorithm
from langsmith.sandbox import SandboxTokenVerifier
from langsmith.sandbox import _verify as langsmith_verify

from openswe.dashboard.settings import Settings
from openswe.database import postgres
from openswe.github import sandbox_budget
from openswe.github.repositories import Repository
from openswe.github.sandbox_access import SandboxGitHubAccess
from openswe.github.sandbox_budget import WINDOW, SandboxGitHubBudget, SandboxGitHubCalls
from openswe.message_queue import QueuedMessage
from openswe.sandboxes import lifecycle, proxy_callback, state
from openswe.sandboxes.proxy_callback import SandboxProxyCallback
from openswe.sandboxes.proxy_callback_routes import ProxyCallbackAnswer, router
from openswe.sandboxes.tool_access import PROXY_CALLBACK_PATH
from openswe.threads import index

pytestmark = pytest.mark.usefixtures("registry_db")

_URL = "https://robin.invalid/dashboard/api/sandbox-proxy-callback"
_ISSUER = "https://api.smith.langchain.com"
_TENANT = str(uuid.uuid4())
_BOX_ID = str(uuid.uuid4())
_KID = "sandbox-callback-key"


class _Signer:
    """LangSmith's callback signing key, and the JWKS that publishes it."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()

    def jwks(self) -> dict[str, object]:
        public = json.loads(OKPAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": _KID, "alg": "EdDSA", "use": "sig"}]}

    def sign(self, body: bytes, *, aud: str = _URL, key: Ed25519PrivateKey | None = None) -> str:
        now = int(time.time())
        claims = {
            "iss": _ISSUER,
            "sub": "langsmith-sandbox-callback",
            "aud": [aud],
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        return jwt.encode(claims, key or self.key, algorithm="EdDSA", headers={"kid": _KID})


def _body(host: str = "github.com", *, tenant_id: str = _TENANT, path: str | None = None) -> bytes:
    identity = {"tenant_id": tenant_id, "sandbox_id": _BOX_ID, "organization_id": str(uuid.uuid4())}
    payload: dict[str, object] = {"host": host, "port": 443, "identity": identity}
    if path is not None:
        payload["request"] = {
            "method": "GET",
            "url": f"https://{host}{path}",
            "scheme": "https",
            "host": host,
            "path": path,
            "headers": {},
        }
    return json.dumps(payload).encode()


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> _Signer:
    """A deployment whose public origin LangSmith calls back, in workspace ``_TENANT``, whose
    sandbox ``_BOX_ID`` is named ``box-1``."""
    signer = _Signer()

    def jwks(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=signer.jwks())

    monkeypatch.setattr(
        langsmith_verify,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx2.AsyncClient(transport=httpx2.MockTransport(jwks)),
            HTTPError=httpx2.HTTPError,
        ),
    )
    monkeypatch.setattr(
        SandboxProxyCallback,
        "_verifier",
        SandboxTokenVerifier(jwks_url="https://ls.invalid/.well-known/jwks.json"),
    )
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://robin.invalid")
    monkeypatch.setattr(proxy_callback, "resolve_tenant_id", AsyncMock(return_value=_TENANT))
    monkeypatch.setattr(
        SandboxProxyCallback,
        "sandbox_name",
        AsyncMock(side_effect=lambda sandbox_id: "box-1" if sandbox_id == _BOX_ID else None),
    )
    return signer


@pytest.fixture
def minted(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """GitHub's App, which mints a token for the repositories asked for, scoped as each
    thread records."""
    monkeypatch.setattr(lifecycle, "thread_token_repositories", state.thread_token_repositories)
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    mint = AsyncMock(return_value=SandboxGitHubAccess("scoped-token", expires_at))
    monkeypatch.setattr(lifecycle, "sandbox_token", mint)
    return mint


async def _thread(sandbox_id: str, **metadata: object) -> str:
    thread_id = str(uuid.uuid4())
    await index.create(
        thread_id,
        runtime="mda",
        metadata={
            "owner_type": "system",
            "visibility": "public",
            "sandbox_owner": "mda",
            "sandbox_id": sandbox_id,
            **metadata,
        },
    )
    return thread_id


@pytest.mark.parametrize("signature", ["missing", "forged", "wrong-audience", "other-body"])
async def test_a_callback_langsmith_did_not_sign_for_this_url_is_refused(
    signer: _Signer, minted: AsyncMock, signature: str
) -> None:
    await _thread("box-1", github_token_repositories=["acme/api"])
    body = _body()
    signed = {
        "missing": None,
        "forged": signer.sign(body, key=Ed25519PrivateKey.generate()),
        "wrong-audience": signer.sign(body, aud="https://elsewhere.invalid/callback"),
        "other-body": signer.sign(_body("api.github.com")),
    }[signature]

    with pytest.raises(HTTPException) as refused:
        await SandboxProxyCallback.answer(body, signed)

    assert refused.value.status_code == 401
    minted.assert_not_awaited()


@pytest.mark.parametrize("binding", ["other-workspace", "unbound", "moved", "external"])
async def test_a_sandbox_no_thread_of_this_workspace_is_bound_to_gets_nothing(
    signer: _Signer, minted: AsyncMock, binding: str
) -> None:
    """Moving a thread to another sandbox takes its credentials from the one it left."""
    if binding in {"other-workspace", "moved"}:
        await _thread("box-2" if binding == "moved" else "box-1")
    if binding == "external":
        await _thread("box-1", sandbox_owner="external")
    body = _body(tenant_id=str(uuid.uuid4()) if binding == "other-workspace" else _TENANT)

    with pytest.raises(HTTPException) as refused:
        await SandboxProxyCallback.answer(body, signer.sign(body))

    assert refused.value.status_code == 403
    minted.assert_not_awaited()


@pytest.mark.parametrize(
    ("host", "scheme"), [("github.com", "Basic"), ("api.github.com", "Bearer")]
)
async def test_a_sandbox_gets_a_token_scoped_to_the_thread_that_owns_it(
    signer: _Signer, minted: AsyncMock, host: str, scheme: str
) -> None:
    """A task worker shares its coordinator's sandbox, and the token is the coordinator's."""
    coordinator = await _thread("box-1", github_token_repositories=["acme/api"])
    await _thread(
        "box-1",
        task_id=str(uuid.uuid4()),
        sandbox_host_thread_id=coordinator,
        github_token_repositories=["acme/api", "acme/internal"],
    )
    body = _body(host)

    app = FastAPI()
    app.include_router(router)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://robin.invalid"
    ) as client:
        response = await client.post(
            PROXY_CALLBACK_PATH,
            content=body,
            headers={"X-LangSmith-Signature-JWT": signer.sign(body)},
        )

    assert response.status_code == 200
    answer = ProxyCallbackAnswer.model_validate(response.json())
    authorization = answer.headers["Authorization"]
    assert authorization.startswith(f"{scheme} ")
    credential = authorization.removeprefix(f"{scheme} ")
    if scheme == "Basic":
        credential = base64.b64decode(credential).decode().removeprefix("x-access-token:")
    assert credential == "scoped-token"
    assert answer.expires_at is not None
    assert minted.await_args is not None
    assert minted.await_args.kwargs["repositories"] == ["acme/api"]


async def test_a_copied_threads_sandbox_keeps_the_originals_credentials(
    signer: _Signer, minted: AsyncMock
) -> None:
    """A copy of a thread keeps its sandbox binding, as ``ThreadRouter._copy`` indexes it,
    and the sandbox stays the original's: its requests get the original's token."""
    await _thread("box-1", github_token_repositories=["acme/api"])
    # The copy's own settings since it was made, so whose token it is shows.
    await _thread("box-1", github_token_repositories=["acme/api", "acme/web"])
    body = _body()

    answer = await SandboxProxyCallback.answer(body, signer.sign(body))

    assert answer.headers["Authorization"].startswith("Basic ")
    assert minted.await_args is not None
    assert minted.await_args.kwargs["repositories"] == ["acme/api"]


def _budget(
    monkeypatch: pytest.MonkeyPatch, mode: str, per_hour: int = 2, brake_percent: int = 20
) -> None:
    settings = Settings(
        {
            "sandbox_github_budget_mode": mode,
            "sandbox_github_budget_per_hour": per_hour,
            "sandbox_github_brake_percent": brake_percent,
        }
    )
    monkeypatch.setattr(
        sandbox_budget, "cached_instance_settings", AsyncMock(return_value=settings)
    )


async def _call(signer: _Signer, path: str = "/repos/acme/api/pulls") -> int:
    body = _body("api.github.com", path=path)
    try:
        await SandboxProxyCallback.answer(body, signer.sign(body))
    except HTTPException as refused:
        return refused.status_code
    return 200


async def _notes(thread_id: str) -> list[str]:
    return [str(queued.content) for queued in await QueuedMessage.for_thread(thread_id)]


def test_metering_gives_api_calls_a_callback_the_proxy_calls_every_time(
    signer: _Signer,
) -> None:
    metered = SandboxProxyCallback.proxy_config(None, metered=True)["callbacks"]
    assert isinstance(metered, list) and isinstance(metered[0], dict)
    assert metered[0]["match_hosts"] == ["api.github.com"]
    assert metered[0]["full_request"] is True
    assert len(metered) == 2

    unmetered = SandboxProxyCallback.proxy_config({"callbacks": metered})["callbacks"]
    assert isinstance(unmetered, list) and len(unmetered) == 1
    assert isinstance(unmetered[0], dict) and "full_request" not in unmetered[0]


async def test_enforce_caps_each_thread_and_tells_it_once(
    signer: _Signer, minted: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, "enforce")
    thread_id = await _thread("box-1", github_token_repositories=["acme/api"])

    assert [await _call(signer) for _ in range(4)] == [200, 200, 429, 429]

    notes = await _notes(thread_id)
    assert len(notes) == 1
    assert "budget of 2 GitHub API calls an hour" in notes[0]
    # Refused calls never reached GitHub, so they leave the window as it was.
    assert (await SandboxGitHubCalls.window(thread_id)).total == 2
    # git spends no API quota and is never metered.
    git = _body("github.com", path="/acme/api.git/info/refs")
    assert (await SandboxProxyCallback.answer(git, signer.sign(git))).headers


async def test_observe_counts_without_refusing(
    signer: _Signer, minted: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, "observe")
    thread_id = await _thread("box-1", github_token_repositories=["acme/api"])

    assert [await _call(signer) for _ in range(3)] == [200, 200, 200]
    assert (await SandboxGitHubCalls.window(thread_id)).total == 3
    assert await _notes(thread_id) == []


async def test_brake_refuses_the_low_quota_until_it_resets(
    signer: _Signer, minted: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, "enforce", per_hour=100)
    thread_id = await _thread("box-1", github_token_repositories=["acme/api"])
    budget = await SandboxGitHubBudget.configured()
    reset_at = datetime.now(UTC) + timedelta(minutes=30)
    await budget.sampled("core", limit=15_000, remaining=2_999, reset_at=reset_at)
    await budget.sampled("graphql", limit=5_000, remaining=1_000, reset_at=reset_at)

    minted.reset_mock()
    assert await _call(signer) == 429
    # A refused call resolves no token, which can itself spend quota.
    minted.assert_not_awaited()
    assert await _call(signer, path="/graphql") == 200
    assert "close to its hourly GitHub API limit" in (await _notes(thread_id))[0]


async def test_turning_the_brake_off_lifts_one_already_engaged(
    signer: _Signer, minted: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, "enforce", per_hour=100)
    await _thread("box-1", github_token_repositories=["acme/api"])
    budget = await SandboxGitHubBudget.configured()
    reset_at = datetime.now(UTC) + timedelta(minutes=30)
    await budget.sampled("core", limit=15_000, remaining=2_999, reset_at=reset_at)
    assert await _call(signer) == 429

    _budget(monkeypatch, "enforce", per_hour=100, brake_percent=0)
    assert await _call(signer) == 200


async def test_a_thread_whose_repositories_are_not_installed_reads_anonymously(
    signer: _Signer, minted: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its calls carry no token and spend no installation quota, so the budget leaves them be."""
    _budget(monkeypatch, "enforce", per_hour=1)
    thread_id = await _thread("box-1", github_token_repositories=["acme/public-docs"])
    await Repository.record_github_ids({"acme/public-docs": None}, checked_at=datetime.now(UTC))

    assert [await _call(signer) for _ in range(3)] == [200, 200, 200]
    assert (await SandboxGitHubCalls.window(thread_id)).total == 0


async def test_a_call_metering_cannot_count_goes_through(
    signer: _Signer, minted: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, "enforce")
    await _thread("box-1", github_token_repositories=["acme/api"])
    monkeypatch.setattr(SandboxGitHubBudget, "admit", AsyncMock(side_effect=RuntimeError("down")))

    assert await _call(signer) == 200


async def test_window_keeps_a_minute_until_its_last_call_is_an_hour_old() -> None:
    oldest = (datetime.now(UTC) - WINDOW).replace(second=0, microsecond=0)
    async with postgres.session() as session:
        session.add(SandboxGitHubCalls(thread_id="thread-a", minute=oldest, calls=3))

    assert (await SandboxGitHubCalls.window("thread-a")).total == 3
