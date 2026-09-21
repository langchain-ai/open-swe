import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from agent.api_keys.models import KEY_PREFIX, ApiKey
from agent.database import postgres


def _future(days: int = 30) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


async def _row(key_id: str) -> dict[str, object]:
    async with postgres.connection() as conn:
        return dict(
            (await conn.execute(text("SELECT * FROM api_key WHERE id = :id"), {"id": key_id}))
            .mappings()
            .one()
        )


async def test_create_stores_only_the_digest_and_suffix(registry_db: None) -> None:
    key, secret = await ApiKey.create(
        workspace="core", name="CI", expires_at=_future(), created_by="admin"
    )

    assert secret.startswith(KEY_PREFIX)
    assert len(secret) == len(KEY_PREFIX) + 40
    assert key.key_suffix == secret[-6:]
    assert key.status == "active"

    row = await _row(key.id)
    assert row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in str(row.values())
    assert key.model_dump_json().find(secret) == -1


async def test_authenticate_accepts_a_live_key_and_rejects_every_other_state(
    registry_db: None,
) -> None:
    live, live_secret = await ApiKey.create(
        workspace="core", name="live", expires_at=_future(), created_by="admin"
    )
    revoked, revoked_secret = await ApiKey.create(
        workspace="core", name="revoked", expires_at=_future(), created_by="admin"
    )
    expired, expired_secret = await ApiKey.create(
        workspace="core", name="expired", expires_at=_future(), created_by="admin"
    )
    assert await ApiKey.revoke(revoked.id) is True
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE api_key SET expires_at = :past WHERE id = :id"),
            {"past": datetime.now(UTC) - timedelta(minutes=1), "id": expired.id},
        )

    authenticated = await ApiKey.authenticate(live_secret)
    assert authenticated is not None
    assert authenticated.id == live.id

    assert await ApiKey.authenticate(revoked_secret) is None
    assert await ApiKey.authenticate(expired_secret) is None
    assert await ApiKey.authenticate(KEY_PREFIX + "nosuchkey") is None
    assert await ApiKey.authenticate("not-an-open-swe-key") is None


async def test_revoke_is_idempotent_and_reports_unknown_keys(registry_db: None) -> None:
    key, _ = await ApiKey.create(
        workspace="core", name="CI", expires_at=_future(), created_by="admin"
    )

    assert await ApiKey.revoke(key.id) is True
    first = (await _row(key.id))["revoked_at"]
    assert await ApiKey.revoke(key.id) is True
    assert (await _row(key.id))["revoked_at"] == first
    assert await ApiKey.revoke("00000000000000000000000000000000") is False


async def test_touch_records_last_use(registry_db: None) -> None:
    key, _ = await ApiKey.create(
        workspace="core", name="CI", expires_at=_future(), created_by="admin"
    )
    assert key.last_used_at is None

    await ApiKey.touch(key.id)

    stored = next(
        candidate for candidate in await ApiKey.list_all("core") if candidate.id == key.id
    )
    assert stored.last_used_at is not None


async def test_list_all_filters_by_workspace(registry_db: None) -> None:
    core, _ = await ApiKey.create(
        workspace="core", name="CI", expires_at=_future(), created_by="admin"
    )
    await ApiKey.create(workspace="oss", name="CI", expires_at=_future(), created_by="admin")

    assert [key.id for key in await ApiKey.list_all("core")] == [core.id]
    assert len(await ApiKey.list_all()) == 2
