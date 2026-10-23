import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth.oauth_proxy.models import (
    ClientCode,
    OAuthTransaction,
    ProxyDCRClient,
    UpstreamTokenSet,
)
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.adapters.pydantic.base import BasePydanticAdapter
from pydantic import AnyUrl, BaseModel
from sqlalchemy import select, update

from openswe.database import postgres
from openswe.mcp.token_store import (
    COLLECTIONS,
    MCPOAuthEntryRow,
    SealedStore,
    UnknownOAuthCollection,
)

_COLLECTION = "mcp-upstream-tokens"


async def test_every_collection_the_oauth_proxy_uses_has_a_spec() -> None:
    proxy = GitHubProvider(
        client_id="client",
        client_secret="secret",
        base_url="https://openswe.example",
        client_storage=SealedStore(),
    )
    used = {
        adapter._default_collection
        for adapter in vars(proxy).values()
        if isinstance(adapter, BasePydanticAdapter)
    }
    assert used == set(COLLECTIONS)
    with pytest.raises(UnknownOAuthCollection):
        await SealedStore().get("key", collection="mcp-added-by-an-upgrade")


@pytest.fixture
def oauth_db(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def _token(key: str) -> object:
    value = await SealedStore().get(key, collection=_COLLECTION)
    return None if value is None else value["token"]


async def test_entries_read_as_absent_once_expired_and_later_writes_sweep_them(
    oauth_db: None,
) -> None:
    sealed_store = SealedStore()
    await sealed_store.put("lapsing", {"token": "secret"}, collection=_COLLECTION, ttl=60)
    assert await _token("lapsing") == "secret"
    async with postgres.session() as session:
        await session.execute(
            update(MCPOAuthEntryRow).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    assert await _token("lapsing") is None
    await sealed_store.put("other", {"token": "other"}, collection=_COLLECTION)
    async with postgres.session() as session:
        assert list(await session.scalars(select(MCPOAuthEntryRow.key))) == ["other"]


async def _saved_document[ModelT: BaseModel](
    collection: str, key: str, model: ModelT
) -> tuple[ModelT | None, str]:
    """``model`` saved and read back through the proxy's adapter, and its row's ``entry`` as text."""
    adapter = PydanticAdapter[ModelT](
        key_value=SealedStore(), pydantic_model=type(model), default_collection=collection
    )
    await adapter.put(key, model, ttl=600)
    async with postgres.session() as session:
        entry = (
            await session.execute(
                select(MCPOAuthEntryRow.entry).where(MCPOAuthEntryRow.collection == collection)
            )
        ).scalar_one()
    return await adapter.get(key), json.dumps(entry)


async def test_entries_keep_readable_metadata_and_no_plaintext_secret(oauth_db: None) -> None:
    now = datetime.now(UTC).timestamp()
    tokens = UpstreamTokenSet(
        upstream_token_id="upstream-1",
        access_token="gho_access_secret",
        refresh_token="ghr_refresh_secret",
        refresh_token_expires_at=None,
        expires_at=now + 3600,
        token_type="Bearer",
        scope="repo",
        client_id="client-1",
        created_at=now,
        raw_token_data={"access_token": "gho_access_secret", "id_token": "id_token_secret"},
    )
    code = ClientCode(
        code="authorization_code_secret",
        client_id="client-1",
        redirect_uri="http://localhost:3000/callback",
        code_challenge="challenge",
        code_challenge_method="S256",
        scopes=["repo"],
        idp_tokens={"access_token": "gho_idp_secret"},
        expires_at=now + 300,
        created_at=now,
    )
    transaction = OAuthTransaction(
        txn_id="txn-1",
        client_id="client-1",
        client_redirect_uri="http://localhost:3000/callback",
        client_state="client_state_secret",
        code_challenge="challenge",
        code_challenge_method="S256",
        scopes=["repo"],
        created_at=now,
        proxy_code_verifier="code_verifier_secret",
        csrf_token="csrf_secret",
        consent_token="consent_secret",
    )
    client = ProxyDCRClient(
        client_id="client-1",
        client_secret="client_secret_value",
        redirect_uris=[AnyUrl("http://localhost:3000/callback")],
    )

    read, entry = await _saved_document("mcp-upstream-tokens", "upstream-1", tokens)
    assert read == tokens
    assert "upstream-1" in entry
    assert not {"gho_access_secret", "ghr_refresh_secret", "id_token_secret"} & _words(entry)
    read, entry = await _saved_document("mcp-authorization-codes", code.code, code)
    assert read == code
    assert not {"authorization_code_secret", "gho_idp_secret"} & _words(entry)
    read, entry = await _saved_document("mcp-oauth-transactions", "txn-1", transaction)
    assert read == transaction
    secrets = {"client_state_secret", "code_verifier_secret", "csrf_secret", "consent_secret"}
    assert not secrets & _words(entry)
    read, entry = await _saved_document("mcp-oauth-proxy-clients", "client-1", client)
    assert read == client
    assert "client_secret_value" not in _words(entry)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w-]+", text))
