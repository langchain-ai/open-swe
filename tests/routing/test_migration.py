import json
from uuid import uuid7

import pytest
from sqlalchemy import text

from openswe.database import postgres

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_expand_migration_preserves_bindings_flags_queue_identity_and_legacy_rows() -> None:
    migrations = postgres.load_migrations()
    first, second = uuid7(), uuid7()
    allowed, ordinary, mismatched = uuid7(), uuid7(), uuid7()
    queue, obsolete_queue = uuid7(), uuid7()
    legacy_match, deleted_subscription = uuid7(), uuid7()
    legacy_config = {"thread_id": "legacy-listener", "github_login": "alice", "workspace": "first"}
    async with postgres.connection() as conn:
        await conn.execute(text("CREATE SCHEMA routing_upgrade"))
        await conn.commit()
        for parent in ("f7b177302414", "90f3d690a0d2"):
            await conn.run_sync(postgres.upgrade, migrations, "routing_upgrade", parent)
        await conn.execute(text("SET LOCAL search_path TO routing_upgrade, public"))
        for row_id, slug, configured in (
            (
                first,
                "first",
                {"repository": "acme/allowed", "operator_emails": ["operator@example.com"]},
            ),
            (second, "second", None),
        ):
            await conn.execute(
                text(
                    "INSERT INTO workspace (id, slug, name, merge_queue_config) VALUES (:id, :slug, :slug, CAST(:config AS jsonb))"
                ),
                {
                    "id": row_id,
                    "slug": slug,
                    "config": json.dumps(configured) if configured else None,
                },
            )
        for row_id, name, granted in (
            (allowed, "allowed", True),
            (ordinary, "ordinary", False),
            (mismatched, "mismatched", False),
        ):
            await conn.execute(
                text("INSERT INTO repository (id, key, full_name) VALUES (:id, :name, :name)"),
                {"id": row_id, "name": f"acme/{name}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO workspace_repository (repository_id, workspace_id, may_start_threads) VALUES (:repository, :workspace, :granted)"
                ),
                {"repository": row_id, "workspace": first, "granted": granted},
            )
        for channel, kitchen, ask in (
            ("C0MENTIONS", False, False),
            ("C0KITCHEN", True, False),
            ("C0ASK", False, True),
        ):
            await conn.execute(
                text(
                    "INSERT INTO workspace_slack_channel (channel_id, workspace_id, kitchen, ask) VALUES (:channel, :workspace, :kitchen, :ask)"
                ),
                {"channel": channel, "workspace": first, "kitchen": kitchen, "ask": ask},
            )
        for queue_id, repository, workspace in (
            (queue, allowed, first),
            (obsolete_queue, mismatched, second),
        ):
            await conn.execute(
                text(
                    "INSERT INTO merge_queue (id, workspace_id, repository_id, target_branch) VALUES (:id, :workspace, :repository, 'main')"
                ),
                {"id": queue_id, "workspace": workspace, "repository": repository},
            )
        legacy_workspace_ids = set(await conn.scalars(text("SELECT id FROM workspace")))
        await conn.execute(
            text("""
                INSERT INTO event_match
                    (id, thread_id, subscription_id, source, delivery_id, content, run_config)
                VALUES (:id, 'legacy-listener', :subscription, 'github', 'legacy-delivery',
                        'deployment finished', CAST(:config AS jsonb))
            """),
            {
                "id": legacy_match,
                "subscription": deleted_subscription,
                "config": json.dumps(legacy_config),
            },
        )
        await conn.commit()
        with pytest.raises(RuntimeError, match="Drain undelivered legacy webhook matches"):
            await conn.run_sync(postgres.upgrade, migrations, "routing_upgrade", "heads")
        await conn.execute(text("SET LOCAL search_path TO routing_upgrade, public"))
        assert (
            await conn.scalar(text("SELECT to_regclass('routing_upgrade.integration_repository')"))
            is None
        )
        assert (
            await conn.scalar(
                text("SELECT delivered_at FROM event_match WHERE id = :id"), {"id": legacy_match}
            )
            is None
        )
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM event_subscription WHERE id = :id"),
                {"id": deleted_subscription},
            )
            == 0
        )
        await conn.execute(
            text("UPDATE event_match SET delivered_at = clock_timestamp() WHERE id = :id"),
            {"id": legacy_match},
        )
        await conn.commit()
        await conn.run_sync(postgres.upgrade, migrations, "routing_upgrade", "heads")
        await conn.execute(text("SET LOCAL search_path TO routing_upgrade, public"))
        bindings = (
            await conn.execute(
                text(
                    "SELECT r.full_name, b.may_start_threads, b.merge_queue_config, b.id FROM integration_repository b JOIN repository r ON r.id = b.repository_id ORDER BY r.key"
                )
            )
        ).all()
        assert [(name, granted) for name, granted, _, _ in bindings] == [
            ("acme/allowed", True),
            ("acme/mismatched", False),
            ("acme/ordinary", False),
        ]
        assert bindings[0][2] == {
            "repository": "acme/allowed",
            "operator_emails": ["operator@example.com"],
        }
        assert all(config is None for _, _, config, _ in bindings[1:])
        assert all(binding_id.version == 7 for _, _, _, binding_id in bindings)
        assert (
            await conn.execute(
                text("SELECT channel_id, mode FROM integration_slack_channel ORDER BY channel_id")
            )
        ).all() == [("C0ASK", "ask"), ("C0KITCHEN", "kitchen"), ("C0MENTIONS", "mentions")]
        assert (
            await conn.scalar(
                text("SELECT routing_repository_id FROM merge_queue WHERE id = :id"), {"id": queue}
            )
            == bindings[0][3]
        )
        assert (
            await conn.scalar(
                text("SELECT routing_repository_id FROM merge_queue WHERE id = :id"),
                {"id": obsolete_queue},
            )
            is None
        )
        assert set(await conn.scalars(text("SELECT id FROM workspace"))) == legacy_workspace_ids
        assert await conn.scalar(text("SELECT count(*) FROM workspace_repository")) == 3
        assert await conn.scalar(text("SELECT count(*) FROM workspace_slack_channel")) == 3
        nullable = (
            await conn.execute(
                text(
                    "SELECT table_name, is_nullable FROM information_schema.columns WHERE table_schema = 'routing_upgrade' AND column_name = 'workspace_id' AND table_name IN ('merge_queue', 'automation', 'task', 'suggested_task', 'event_subscription')"
                )
            )
        ).all()
        assert len(nullable) == 5 and all(value == "YES" for _, value in nullable)
        constraints = set(
            await conn.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'routing_upgrade.event_log'::regclass"
                )
            )
        )
        assert "event_log_admin_workspace_check" not in constraints
        assert "event_log_private_user_check" in constraints
        assert (
            await conn.execute(
                text("""
                SELECT source, subscription_id, content, run_config, delivered_at IS NOT NULL
                FROM event_match WHERE id = :id
            """),
                {"id": legacy_match},
            )
        ).one() == ("github", deleted_subscription, "deployment finished", legacy_config, True)
