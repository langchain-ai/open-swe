"""UI invalidations"""

from alembic import op

revision = "1c56fc371df9"
down_revision = "f8280ead09c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ui_invalidation (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            topic text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX ui_invalidation_topic_idx ON ui_invalidation (topic, created_at)")
    op.execute("CREATE INDEX ui_invalidation_created_idx ON ui_invalidation (created_at)")
    for statement in (
        "COMMENT ON TABLE ui_invalidation IS 'Dashboard cache invalidations: one row per "
        "topic a committed write changed, also sent on pg_notify channel "
        "open_swe_ui_invalidations. Read only to replay what a reconnecting browser missed; "
        "rows older than a day are pruned.'",
        "COMMENT ON COLUMN ui_invalidation.topic IS 'What changed, e.g. workspaces or "
        "pr/<uuid>. Carries no data: a browser refetches whatever reads that topic.'",
        "COMMENT ON COLUMN ui_invalidation.created_at IS 'Database clock at insert. Replay "
        "windows are durations against this clock, so browser clock skew never matters.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
