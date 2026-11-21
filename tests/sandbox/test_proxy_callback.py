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

from openswe.github.sandbox_access import SandboxGitHubAccess
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


def _body(host: str = "github.com", *, tenant_id: str = _TENANT) -> bytes:
    identity = {"tenant_id": tenant_id, "sandbox_id": _BOX_ID, "organization_id": str(uuid.uuid4())}
    return json.dumps({"host": host, "port": 443, "identity": identity}).encode()


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
