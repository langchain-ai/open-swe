"""Decouple integration routing from environments"""

import json
from uuid import uuid7

from alembic import op
from sqlalchemy import text

revision = "4b3643ee9665"
down_revision = ["f7b177302414", "90f3d690a0d2"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '1min'")
    connection = op.get_bind()
    if connection.scalar(
        text("""
            SELECT EXISTS (
                SELECT 1 FROM event_match
                WHERE delivered_at IS NULL
                  AND matched_at >= clock_timestamp() - interval '14 days'
                  AND source NOT IN ('sandbox', 'subscription_timeout')
                  AND jsonb_typeof(run_config->'_event_audience') IS DISTINCT FROM 'object'
            )
        """)
    ):
        raise RuntimeError(
            "Drain undelivered legacy webhook matches under the old runtime before upgrading; "
            "see docs/ROBIN_ROLLOUT.md#integration-routing-cutover"
        )
    op.execute("""
        CREATE TABLE integration_repository (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
            may_start_threads boolean NOT NULL DEFAULT false,
            merge_queue_config jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX integration_repository_repository_id_key ON integration_repository(repository_id)"
    )
    op.execute("""
        CREATE TABLE integration_slack_channel (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel(id) ON DELETE CASCADE,
            mode text NOT NULL DEFAULT 'mentions' CHECK (mode IN ('mentions', 'kitchen', 'ask')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX integration_slack_channel_channel_id_key ON integration_slack_channel(channel_id)"
    )
    op.execute("""
        INSERT INTO slack_channel (id)
        SELECT channel_id FROM workspace_slack_channel
        ON CONFLICT (id) DO NOTHING
    """)
    repositories = (
        connection.execute(
            text("""
        SELECT wr.repository_id, wr.may_start_threads, wr.linked_at,
               CASE WHEN lower(w.merge_queue_config->>'repository') = r.key
                    THEN w.merge_queue_config END AS merge_queue_config
        FROM workspace_repository wr
        JOIN workspace w ON w.id = wr.workspace_id
        JOIN repository r ON r.id = wr.repository_id
    """)
        )
        .mappings()
        .all()
    )
    for row in repositories:
        connection.execute(
            text("""
            INSERT INTO integration_repository
                (id, repository_id, may_start_threads, merge_queue_config, created_at, updated_at)
            VALUES (:id, :repository_id, :may_start_threads, CAST(:merge_queue_config AS jsonb), :linked_at, :linked_at)
        """),
            {
                **row,
                "id": uuid7(),
                "merge_queue_config": json.dumps(row["merge_queue_config"])
                if row["merge_queue_config"] is not None
                else None,
            },
        )
    channels = (
        connection.execute(
            text("""
        SELECT c.channel_id, CASE WHEN c.ask THEN 'ask' WHEN c.kitchen THEN 'kitchen'
               ELSE 'mentions' END AS mode, c.linked_at
        FROM workspace_slack_channel c JOIN workspace w ON w.id = c.workspace_id
    """)
        )
        .mappings()
        .all()
    )
    for row in channels:
        connection.execute(
            text("""
            INSERT INTO integration_slack_channel
                (id, channel_id, mode, created_at, updated_at)
            VALUES (:id, :channel_id, :mode, :linked_at, :linked_at)
        """),
            {**row, "id": uuid7()},
        )
    op.execute(
        "ALTER TABLE merge_queue ADD COLUMN routing_repository_id uuid REFERENCES integration_repository(id) ON DELETE SET NULL"
    )
    op.execute("""
        UPDATE merge_queue q SET routing_repository_id = i.id
        FROM integration_repository i, workspace_repository wr
        WHERE i.repository_id = q.repository_id
          AND wr.repository_id = q.repository_id AND wr.workspace_id = q.workspace_id
    """)
    op.execute("ALTER TABLE merge_queue ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute("ALTER TABLE automation ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute("ALTER TABLE task ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute("ALTER TABLE suggested_task ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute("ALTER TABLE event_subscription ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute("ALTER TABLE event_log DROP CONSTRAINT event_log_admin_workspace_check")


def downgrade() -> None:
    raise NotImplementedError
