import json
from uuid import uuid7

import pytest
from sqlalchemy import text

from openswe.database import postgres

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_routing_migrations_reset_channels_and_preserve_repositories_and_history() -> None:
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
        subscription_sources = {
            uuid7(): (["workspace"], ["robin"]),
            uuid7(): (["github", "workspace"], ["github", "robin"]),
            uuid7(): (["github"], ["github"]),
            uuid7(): ([], []),
            uuid7(): (["robin"], ["robin"]),
        }
        for subscription_id, (sources, _) in subscription_sources.items():
            await conn.execute(
                text("""
                    INSERT INTO event_subscription (
                        id, thread_id, workspace_id, repository_id, sources,
                        event_types, payload_match, multitask_strategy, instructions,
                        one_shot, run_config, expires_at
                    ) VALUES (
                        :id, 'legacy-listener', :workspace, :repository, :sources,
                        ARRAY['task.suggested'], '{"summary": "follow up"}', 'enqueue',
                        'Handle the suggestion', true, CAST(:config AS jsonb),
                        clock_timestamp() + interval '1 hour'
                    )
                """),
                {
                    "id": subscription_id,
                    "workspace": first,
                    "repository": allowed,
                    "sources": sources,
                    "config": json.dumps(legacy_config),
                },
            )
        subscriptions_before = (
            (await conn.execute(text("SELECT * FROM event_subscription ORDER BY id")))
            .mappings()
            .all()
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
        subscriptions_after = (
            (await conn.execute(text("SELECT * FROM event_subscription ORDER BY id")))
            .mappings()
            .all()
        )
        assert subscriptions_after == [
            {**row, "sources": subscription_sources[row["id"]][1]} for row in subscriptions_before
        ]
        bindings = (
            await conn.execute(
                text(
                    "SELECT r.full_name, b.merge_queue_config, b.id FROM integration_repository b JOIN repository r ON r.id = b.repository_id ORDER BY r.key"
                )
            )
        ).all()
        assert [name for name, _, _ in bindings] == [
            "acme/allowed",
            "acme/mismatched",
            "acme/ordinary",
        ]
        assert bindings[0][1] == {
            "repository": "acme/allowed",
            "operator_emails": ["operator@example.com"],
        }
        assert all(config is None for _, config, _ in bindings[1:])
        assert all(binding_id.version == 7 for _, _, binding_id in bindings)
        assert await conn.scalar(text("SELECT count(*) FROM integration_slack_channel")) == 0
        assert set(
            await conn.scalars(text("SELECT id FROM slack_channel WHERE id LIKE 'C0%'"))
        ) == {"C0ASK", "C0KITCHEN", "C0MENTIONS"}
        assert (
            await conn.scalar(
                text("SELECT routing_repository_id FROM merge_queue WHERE id = :id"), {"id": queue}
            )
            == bindings[0][2]
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


@pytest.mark.parametrize("enabled", [True, False])
async def test_an_enabled_incident_policy_becomes_its_prefixs_channel_rule(enabled: bool) -> None:
    migrations = postgres.load_migrations()
    async with postgres.connection() as conn:
        await conn.execute(text("CREATE SCHEMA incident_rules"))
        await conn.commit()
        for parent in (
            "59c9a4be00db",
            "758a156e43d0",
            "5123042028cf",
            "2d6d31428885",
            "c9501de19ea1",
        ):
            await conn.run_sync(postgres.upgrade, migrations, "incident_rules", parent)
        await conn.execute(text("SET LOCAL search_path TO incident_rules, public"))
        await conn.execute(
            text(
                "INSERT INTO incident_policy (id, version, policy) VALUES (:id, 3, CAST(:policy AS jsonb))"
            ),
            {"id": uuid7(), "policy": json.dumps({"enabled": enabled, "channel_prefix": "sev-"})},
        )
        await conn.commit()
        await conn.run_sync(postgres.upgrade, migrations, "incident_rules", "heads")
        await conn.execute(text("SET LOCAL search_path TO incident_rules, public"))
        rules = (
            await conn.execute(text("SELECT prefix, kickoff FROM integration_slack_channel_rule"))
        ).all()
        retained = await conn.scalar(
            text("SELECT count(*) FROM incident_policy WHERE policy->>'channel_prefix' = 'sev-'")
        )
    assert retained == 1
    assert [prefix for prefix, _ in rules] == (["sev-"] if enabled else [])
    assert all("Investigate the incident" in kickoff for _, kickoff in rules)
