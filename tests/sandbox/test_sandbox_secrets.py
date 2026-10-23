"""Personal sandbox secrets reach only their owner's private sandboxes, never as env values."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx2
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from openswe.encryption import encrypt_token
from openswe.sandbox_secrets import proxy
from openswe.sandbox_secrets.models import (
    PLACEHOLDER,
    SandboxSecret,
    SecretRejected,
    SecretSettings,
    validate_name,
)
from openswe.sandboxes.providers.langsmith import configure_sandbox_proxy

OWNER = uuid4()
OTHER = uuid4()


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> SandboxSecret:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    stored = SandboxSecret(
        user_id=OWNER,
        name="LANGSMITH_API_KEY",
        encrypted_value=encrypt_token("lsv2-real"),
        hosts=["api.smith.langchain-dev.com"],
        header_name="x-api-key",
        header_format="{value}",
    )
    owned_by = AsyncMock(side_effect=lambda user_id: [stored] if user_id == OWNER else [])
    monkeypatch.setattr(SandboxSecret, "owned_by", owned_by)
    return stored


def _threads(monkeypatch: pytest.MonkeyPatch, **threads: dict[str, object]) -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(side_effect=lambda tid: {"metadata": threads[tid]}))
    )
    monkeypatch.setattr(proxy, "get_client", lambda: client)


def _private(owner: object, **extra: object) -> dict[str, object]:
    return {"visibility": "private", "owner_type": "user", "owner_user_id": str(owner), **extra}


@pytest.mark.parametrize(
    ("threads", "expected"),
    [
        ({"t": _private(OWNER)}, True),
        ({"t": {**_private(OWNER), "visibility": "public"}}, False),
        ({"t": {**_private(OWNER), "owner_type": "system"}}, False),
        ({"t": _private(OWNER, sandbox_host_thread_id="h"), "h": _private(OWNER)}, True),
        ({"t": _private(OWNER, sandbox_host_thread_id="h"), "h": _private(OTHER)}, False),
        (
            {
                "t": _private(OWNER, sandbox_host_thread_id="h"),
                "h": {**_private(OWNER), "visibility": "public"},
            },
            False,
        ),
    ],
)
async def test_secrets_apply_only_to_their_owners_private_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    secret: SandboxSecret,
    threads: dict[str, dict[str, object]],
    expected: bool,
) -> None:
    _threads(monkeypatch, **threads)

    rules = await proxy.thread_secret_rules("t")

    assert bool(rules) is expected
    if expected:
        assert rules[0]["env_vars"] == {"LANGSMITH_API_KEY": PLACEHOLDER}
        assert rules[0]["headers"] == [
            {"name": "x-api-key", "type": "opaque", "value": "lsv2-real"}
        ]


async def test_rejected_secrets_do_not_cost_the_sandbox(
    monkeypatch: pytest.MonkeyPatch, secret: SandboxSecret
) -> None:
    _threads(monkeypatch, t=_private(OWNER))
    request = httpx2.Request("PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/box")
    rejected = MagicMock()
    rejected.raise_for_status.side_effect = httpx2.HTTPStatusError(
        "bad rule", request=request, response=httpx2.Response(422, request=request)
    )
    client = MagicMock()
    client.patch = AsyncMock(side_effect=[rejected, MagicMock()])
    with (
        patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as client_cls,
        patch("openswe.sandboxes.tool_access.tool_proxy_rule", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
    ):
        client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await configure_sandbox_proxy("box", "token", thread_id="t")

    sent = [call.kwargs["json"]["proxy_config"]["rules"] for call in client.patch.call_args_list]
    assert sent[0][0]["name"] == "user-secret-langsmith-api-key"
    assert not any(rule["name"].startswith("user-secret-") for rule in sent[1])


@pytest.mark.parametrize(
    "name", ["GH_TOKEN", "OPEN_SWE_TOOLS_URL", "AWS_SECRET_ACCESS_KEY", "lower"]
)
def test_reserved_or_invalid_names_are_rejected(name: str) -> None:
    with pytest.raises(SecretRejected):
        validate_name(name)


@pytest.mark.parametrize("host", ["api.github.com", "*.github.com", "https://x.com", "*.com"])
def test_github_and_malformed_hosts_are_rejected(host: str) -> None:
    with pytest.raises(ValidationError):
        SecretSettings(hosts=[host])
