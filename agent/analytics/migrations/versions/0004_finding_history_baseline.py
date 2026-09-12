from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SQL = (
    "-- Finding history must survive raw-event retention: the projection rebuild in\n-- ingestion derives resolved/dismissed/reopen history from the events table,\n-- which is pruned after ANALYTICS_RAW_EVENT_MONTHS. Snapshot the current\n-- durable history into baseline columns and have the rebuild merge on top of\n-- them instead of replacing them.\nALTER TABLE finding_projection\n    ADD COLUMN IF NOT EXISTS history_baseline_at timestamptz,\n    ADD COLUMN IF NOT EXISTS history_baseline_resolved_at timestamptz,\n    ADD COLUMN IF NOT EXISTS history_baseline_dismissed_at timestamptz,\n    ADD COLUMN IF NOT EXISTS history_baseline_reopened_count integer NOT NULL DEFAULT 0",
    "UPDATE finding_projection\nSET history_baseline_at = clock_timestamp(),\n    history_baseline_resolved_at = resolved_at,\n    history_baseline_dismissed_at = dismissed_at,\n    history_baseline_reopened_count = reopened_count\nWHERE resolved_at IS NOT NULL\n   OR dismissed_at IS NOT NULL\n   OR reopened_count > 0",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
