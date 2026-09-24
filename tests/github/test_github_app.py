import asyncio
import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import NoReturn, TypedDict
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet

from agent.config import ENV
from agent.encryption import decrypt_token, encrypt_token
from agent.github import app as github_app
from tests.conftest import FakeStore
from tests.support.github_sdk import mock_github_sdk

_SHARED_TOKENS = ["github_app_tokens", "v1"]
_TOKEN_LIFETIME = timedelta(hours=1)
# Far enough on that a token minted now has entered its last minutes, where it is
# no longer reused.
_PAST_REUSE_CUTOFF = _TOKEN_LIFETIME - github_app._TOKEN_CACHE_MARGIN + timedelta(seconds=1)


def _advance_clock(monkeypatch: pytest.MonkeyPatch, by: timedelta) -> None:
    later = github_app._now() + by
    monkeypatch.setattr(github_app, "_now", lambda: later)


@pytest.fixture(autouse=True)
def app_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "test-key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "2")
    # A key exported in the developer's shell would send scoped tokens to the real Store.
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    github_app.clear_app_token_cache()
    yield
    github_app.clear_app_token_cache()


@pytest.fixture
def mints(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """GitHub mints a new hour-long token per request; returns the request bodies."""
    bodies: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        expires_at = (github_app._now() + _TOKEN_LIFETIME).isoformat()
        return httpx.Response(
            201, json={"token": f"ghs_minted-{len(bodies)}", "expires_at": expires_at}
        )

    mock_github_sdk(monkeypatch, handle)
    return bodies


@pytest.fixture
def shared_store(monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore) -> FakeStore:
    """The Store workers share tokens through, with an encryption key configured."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return fake_store


@pytest.mark.parametrize("kind", ["org", "repo"])
async def test_resolves_installation_with_escaped_names(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        expected = (
            "/orgs/secondary%2Forg/installation"
            if kind == "org"
            else "/repos/acme/private%2Frepo/installation"
        )
        assert request.url.raw_path.decode() == expected
        assert request.headers["authorization"] == "Bearer test-app-jwt"
        return httpx.Response(200, json={"id": 3})

    mock_github_sdk(monkeypatch, handle)
    if kind == "org":
        result = await github_app.get_github_app_installation_id_for_org("secondary/org")
    else:
        result = await github_app.get_github_app_installation_id_for_repo("acme", "private/repo")
    assert result == 3


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_installation_lookup_fails_closed(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    mock_github_sdk(
        monkeypatch, lambda request: httpx.Response(status, json={"message": "unavailable"})
    )
    assert await github_app.get_github_app_installation_id_for_org("acme") is None
    assert await github_app.get_github_app_installation_id_for_repo("acme", "api") is None


@pytest.mark.parametrize("minutes,expected_requests", [(60, 1), (2, 2)])
async def test_token_cache_respects_expiry(
    monkeypatch: pytest.MonkeyPatch, minutes: int, expected_requests: int
) -> None:
    expires_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201, json={"token": f"token-{len(requests)}", "expires_at": expires_at}
        )

    mock_github_sdk(monkeypatch, handle)
    first, expiry = await github_app.get_github_app_installation_token_with_expiry()
    second, _ = await github_app.get_github_app_installation_token_with_expiry()
    assert first == "token-1"
    assert second == f"token-{expected_requests}"
    assert expiry == expires_at
    assert len(requests) == expected_requests


async def test_cache_separates_repository_installation_and_permission_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    requests: list[tuple[str, dict[str, object]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            201, json={"token": f"token-{len(requests)}", "expires_at": expires_at}
        )

    mock_github_sdk(monkeypatch, handle)
    for _ in range(2):
        for installation_id in (2, 3):
            for ids in ([11], [22]):
                for permission in ("read", "write"):
                    token = await github_app.get_github_app_installation_token(
                        installation_id=installation_id,
                        repository_ids=ids,
                        permissions={"contents": permission},
                    )
                    assert token is not None
    assert len(requests) == 8
    assert len({(path, json.dumps(body, sort_keys=True)) for path, body in requests}) == 8


async def test_repository_names_and_full_installation_have_distinct_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        scopes.append(json.loads(request.content))
        return httpx.Response(
            201, json={"token": f"token-{len(scopes)}", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    for names in (["a"], ["b"], ["a"], None):
        assert await github_app.get_github_app_installation_token(repositories=names)
    assert scopes == [{"repositories": ["a"]}, {"repositories": ["b"]}, {}]


async def test_token_request_preserves_repository_ids_and_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/app/installations/3/access_tokens"
        assert json.loads(request.content) == {
            "repository_ids": [123],
            "permissions": {"contents": "write", "workflows": "write"},
        }
        return httpx.Response(201, json={"token": "token", "expires_at": "2099-01-01T00:00:00Z"})

    mock_github_sdk(monkeypatch, handle)
    token, expiry = await github_app.get_github_app_installation_token_with_expiry(
        installation_id=3,
        repository_ids=[123],
        repositories=["ignored"],
        permissions={"workflows": "write", "contents": "write"},
    )
    assert (token, expiry) == ("token", "2099-01-01T00:00:00Z")


@pytest.mark.parametrize("status", [403, 500])
async def test_token_failure_never_returns_cached_broader_access(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("repository_ids"):
            return httpx.Response(status, json={"message": "unavailable"})
        return httpx.Response(
            201, json={"token": "broad-token", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    assert await github_app.get_github_app_installation_token() == "broad-token"
    assert await github_app.get_github_app_installation_token(repository_ids=[123]) is None


async def test_unknown_permissions_cannot_be_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid permissions must not reach GitHub")

    mock_github_sdk(monkeypatch, handle)
    assert (
        await github_app.get_github_app_installation_token(permissions={"unknown": "read"}) is None
    )


async def test_second_worker_reuses_the_token_minted_for_each_scope(
    shared_store: FakeStore, mints: list[dict[str, object]]
) -> None:
    async def resolve_scopes() -> list[tuple[str | None, str | None]]:
        return [
            await github_app.get_github_app_installation_token_with_expiry(repository_ids=[11]),
            await github_app.get_github_app_installation_token_with_expiry(repository_ids=[22]),
            await github_app.get_github_app_installation_token_with_expiry(
                installation_id=3, repository_ids=[11]
            ),
            await github_app.get_github_app_installation_token_with_expiry(
                repository_ids=[11], permissions={"contents": "write"}
            ),
            await github_app.get_github_app_installation_token_with_expiry(
                repositories=["acme/api"]
            ),
        ]

    first = await resolve_scopes()
    github_app.clear_app_token_cache()  # a second worker, with an empty in-process cache
    second = await resolve_scopes()

    assert second == first
    assert len({token for token, _ in first}) == len(mints) == 5


async def _unavailable(*_args: object) -> NoReturn:
    raise RuntimeError("store unavailable")


async def test_store_hit_is_kept_in_process(
    shared_store: FakeStore, mints: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    minted = await github_app.get_github_app_installation_token(repository_ids=[11])
    github_app.clear_app_token_cache()  # a second worker, with an empty in-process cache
    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == minted

    monkeypatch.setattr(shared_store, "get_item", _unavailable)

    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == minted
    assert len(mints) == 1


async def test_token_failure_never_returns_shared_broader_access(
    shared_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("repository_ids") == [11]:
            return httpx.Response(403, json={"message": "unavailable"})
        return httpx.Response(
            201, json={"token": "broad-token", "expires_at": "2099-01-01T00:00:00Z"}
        )

    mock_github_sdk(monkeypatch, handle)
    assert await github_app.get_github_app_installation_token(repository_ids=[11, 22]) == (
        "broad-token"
    )
    assert len(shared_store.values(_SHARED_TOKENS)) == 1
    github_app.clear_app_token_cache()  # a second worker, with an empty in-process cache

    assert await github_app.get_github_app_installation_token(repository_ids=[11]) is None


async def test_store_holds_only_an_encrypted_token_under_an_opaque_key(
    shared_store: FakeStore, mints: list[dict[str, object]]
) -> None:
    token = await github_app.get_github_app_installation_token(repositories=["acme/secret-repo"])

    stored = shared_store.values(_SHARED_TOKENS)
    assert token == "ghs_minted-1"
    assert len(stored) == 1
    assert token not in json.dumps(stored)
    assert "acme/secret-repo" not in next(iter(stored))


def _expired(item: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    _advance_clock(monkeypatch, _PAST_REUSE_CUTOFF)
    return item


def _decrypted_payload(item: dict[str, object]) -> str:
    encrypted = item["encrypted_payload"]
    assert isinstance(encrypted, str)
    return decrypt_token(encrypted)


def _encrypted_long_ago(plaintext: str) -> str:
    """``plaintext`` encrypted under the configured key two hours ago."""
    key = ENV.TOKEN_ENCRYPTION_KEY.require().encode()
    two_hours_ago = int(time.time()) - 2 * 3600
    return Fernet(key).encrypt_at_time(plaintext.encode(), two_hours_ago).decode()


def _stale(item: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """A record left from long ago: past its cutoff and encrypted over an hour back."""
    _advance_clock(monkeypatch, _PAST_REUSE_CUTOFF)
    return item | {"encrypted_payload": _encrypted_long_ago(_decrypted_payload(item))}


def _under_another_key(
    item: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return item


def _malformed(item: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return item | {"good_until": "not-a-timestamp"}


@pytest.mark.parametrize(
    ("spoil", "warnings"),
    [(_expired, 0), (_stale, 0), (_under_another_key, 1), (_malformed, 1)],
    ids=["expired", "stale", "undecryptable", "malformed"],
)
async def test_unusable_stored_token_is_replaced_by_a_fresh_one(
    shared_store: FakeStore,
    mints: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    spoil: Callable[[dict[str, object], pytest.MonkeyPatch], dict[str, object]],
    warnings: int,
) -> None:
    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == "ghs_minted-1"
    [(store_key, item)] = shared_store.values(_SHARED_TOKENS).items()
    shared_store.seed(_SHARED_TOKENS, store_key, spoil(item, monkeypatch))

    github_app.clear_app_token_cache()
    caplog.clear()
    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == "ghs_minted-2"
    assert len(_warnings(caplog)) == warnings
    github_app.clear_app_token_cache()
    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == "ghs_minted-2"
    assert len(mints) == 2


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.WARNING]


class _TokenRequest(TypedDict, total=False):
    repository_ids: list[int]
    permissions: dict[str, str]


@pytest.mark.parametrize(
    ("donor", "target"),
    [
        (
            {"repository_ids": [11], "permissions": {"contents": "write"}},
            {"repository_ids": [11], "permissions": {"contents": "read"}},
        ),
        ({"repository_ids": [22]}, {"repository_ids": [11]}),
    ],
    ids=["write-token-in-read-slot", "other-repository-token"],
)
async def test_token_copied_into_another_scopes_slot_is_not_served(
    shared_store: FakeStore,
    mints: list[dict[str, object]],
    caplog: pytest.LogCaptureFixture,
    donor: _TokenRequest,
    target: _TokenRequest,
) -> None:
    donated = await github_app.get_github_app_installation_token(**donor)
    [donor_slot] = shared_store.values(_SHARED_TOKENS)
    await github_app.get_github_app_installation_token(**target)
    [target_slot] = set(shared_store.values(_SHARED_TOKENS)) - {donor_slot}
    shared_store.seed(_SHARED_TOKENS, target_slot, shared_store.values(_SHARED_TOKENS)[donor_slot])

    github_app.clear_app_token_cache()  # a second worker, with an empty in-process cache
    caplog.clear()
    token = await github_app.get_github_app_installation_token(**target)

    assert donated == "ghs_minted-1"
    assert token == "ghs_minted-3"
    assert len(_warnings(caplog)) == 1


async def test_tampered_plaintext_expiry_can_neither_extend_nor_redate_a_token(
    shared_store: FakeStore, mints: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    minted = await github_app.get_github_app_installation_token_with_expiry(repository_ids=[11])
    [(store_key, item)] = shared_store.values(_SHARED_TOKENS).items()
    tomorrow = (github_app._now() + timedelta(days=1)).isoformat()
    shared_store.seed(
        _SHARED_TOKENS, store_key, item | {"expires_at": tomorrow, "good_until": tomorrow}
    )

    github_app.clear_app_token_cache()  # a second worker, with an empty in-process cache
    assert (
        await github_app.get_github_app_installation_token_with_expiry(repository_ids=[11])
        == minted
    )

    _advance_clock(monkeypatch, _PAST_REUSE_CUTOFF)
    github_app.clear_app_token_cache()
    token, _ = await github_app.get_github_app_installation_token_with_expiry(repository_ids=[11])
    assert token == "ghs_minted-2"


async def test_payload_encrypted_over_an_hour_ago_is_not_served(
    shared_store: FakeStore, mints: list[dict[str, object]]
) -> None:
    await github_app.get_github_app_installation_token(repository_ids=[11])
    [(store_key, item)] = shared_store.values(_SHARED_TOKENS).items()
    # Bound to this slot and good until 2099, but encrypted two hours ago.
    forever = "2099-01-01T00:00:00Z"
    planted = json.loads(_decrypted_payload(item)) | {"token": "ghs_planted", "good_until": forever}
    shared_store.seed(
        _SHARED_TOKENS,
        store_key,
        item
        | {"encrypted_payload": _encrypted_long_ago(json.dumps(planted)), "good_until": forever},
    )

    github_app.clear_app_token_cache()  # a second worker, with an empty in-process cache
    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == "ghs_minted-2"
    github_app.clear_app_token_cache()
    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == "ghs_minted-2"


async def test_unreadable_payload_falls_back_without_logging_the_token(
    shared_store: FakeStore, mints: list[dict[str, object]], caplog: pytest.LogCaptureFixture
) -> None:
    await github_app.get_github_app_installation_token(repository_ids=[11])
    [(store_key, item)] = shared_store.values(_SHARED_TOKENS).items()
    # Decrypts, but binds the planted token to no slot or expiry.
    planted = encrypt_token(json.dumps({"token": "ghs_planted"}))
    shared_store.seed(_SHARED_TOKENS, store_key, item | {"encrypted_payload": planted})
    github_app.clear_app_token_cache()
    caplog.clear()

    assert await github_app.get_github_app_installation_token(repository_ids=[11]) == "ghs_minted-2"

    assert len(_warnings(caplog)) == 1
    assert "ghs_planted" not in caplog.text


@pytest.mark.parametrize(
    "request_scope",
    [{}, {"repository_ids": []}, {"permissions": {"contents": "read"}}],
    ids=["installation", "empty-repository-list", "permissions-only"],
)
async def test_installation_wide_token_is_never_shared(
    shared_store: FakeStore,
    mints: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    request_scope: _TokenRequest,
) -> None:
    reads = AsyncMock(wraps=shared_store.get_item)
    monkeypatch.setattr(shared_store, "get_item", reads)

    first = await github_app.get_github_app_installation_token(**request_scope)
    github_app.clear_app_token_cache()
    second = await github_app.get_github_app_installation_token(**request_scope)

    assert (first, second) == ("ghs_minted-1", "ghs_minted-2")
    assert shared_store.items == {}
    reads.assert_not_awaited()


@pytest.mark.parametrize("encryption_key", [None, ""], ids=["unset", "empty"])
async def test_without_encryption_key_scoped_tokens_are_never_shared(
    fake_store: FakeStore,
    mints: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    encryption_key: str | None,
) -> None:
    if encryption_key is not None:
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", encryption_key)
    reads = AsyncMock(wraps=fake_store.get_item)
    monkeypatch.setattr(fake_store, "get_item", reads)

    first = await github_app.get_github_app_installation_token(repository_ids=[11])
    github_app.clear_app_token_cache()
    second = await github_app.get_github_app_installation_token(repository_ids=[11])

    assert (first, second) == ("ghs_minted-1", "ghs_minted-2")
    assert fake_store.items == {}
    reads.assert_not_awaited()


@pytest.mark.parametrize("operation", ["get_item", "put_item"])
async def test_slow_store_falls_back_like_a_failing_one(
    shared_store: FakeStore,
    mints: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    async def hang(*_args: object) -> NoReturn:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(shared_store, operation, hang)
    monkeypatch.setattr(github_app, "_STORE_TIMEOUT_SECONDS", 0.01)

    async with asyncio.timeout(1):  # a lookup a stalled Store can hold up hangs here
        token = await github_app.get_github_app_installation_token(repository_ids=[11])

    assert token == "ghs_minted-1"
    assert len(_warnings(caplog)) == 1


async def test_store_outage_falls_back_to_minting_without_logging_the_token(
    shared_store: FakeStore,
    mints: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(shared_store, "get_item", _unavailable)
    monkeypatch.setattr(shared_store, "put_item", _unavailable)

    with caplog.at_level(logging.WARNING):
        first = await github_app.get_github_app_installation_token(repository_ids=[11])
        github_app.clear_app_token_cache()
        second = await github_app.get_github_app_installation_token(repository_ids=[11])

    assert (first, second) == ("ghs_minted-1", "ghs_minted-2")
    assert caplog.records, "the fallback must be logged"
    assert "ghs_minted" not in caplog.text
    assert not any("ghs_minted" in repr(vars(record)) for record in caplog.records)
