from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SQL = (
    "CREATE TABLE IF NOT EXISTS additive_event_projection (\n    workspace_id uuid NOT NULL,\n    partition_date date NOT NULL,\n    event_name text NOT NULL,\n    event_count bigint NOT NULL,\n    PRIMARY KEY (workspace_id, partition_date, event_name)\n)",
    "-- Existing summaries can include events that have already expired.\n-- Receipt time distinguishes late ingestion from earlier queue creation.\nWITH retained_events AS (\n    SELECT e.workspace_id, (e.occurred_at AT TIME ZONE 'UTC')::date AS partition_date,\n           e.event_name, COALESCE(r.received_at, e.recorded_at) AS received_at\n    FROM events e LEFT JOIN ingestion_receipts r\n      ON r.workspace_id = e.workspace_id AND r.producer = e.producer\n     AND r.producer_event_id = e.producer_event_id AND r.event_name = e.event_name\n     AND r.event_id = e.event_id\n), summary_totals AS (\n    SELECT s.workspace_id, s.partition_date, counter.key AS event_name,\n           counter.value::bigint + count(e.event_name) AS event_count\n    FROM daily_summaries s\n    CROSS JOIN LATERAL jsonb_each_text(s.counters) counter\n    LEFT JOIN retained_events e\n      ON e.workspace_id = s.workspace_id AND e.partition_date = s.partition_date\n     AND e.event_name = counter.key AND e.received_at > s.recomputed_at\n    WHERE s.family = 'additive' AND s.dimension_key = ''\n    GROUP BY s.workspace_id, s.summary_version, s.partition_date, counter.key, counter.value\n)\nINSERT INTO additive_event_projection (workspace_id, partition_date, event_name, event_count)\nSELECT workspace_id, partition_date, event_name, max(event_count)\nFROM (\n    SELECT workspace_id, partition_date, event_name, count(*) AS event_count\n    FROM retained_events GROUP BY workspace_id, partition_date, event_name\n    UNION ALL\n    SELECT workspace_id, partition_date, event_name, event_count FROM summary_totals\n) counts\nGROUP BY workspace_id, partition_date, event_name\nON CONFLICT (workspace_id, partition_date, event_name) DO UPDATE SET\n    event_count = GREATEST(additive_event_projection.event_count, EXCLUDED.event_count)",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
