"""Live change events"""

from alembic import op

revision = "1c56fc371df9"
down_revision = ["1a27b64154a3", "243390dbd39e", "f7b59c5091a8", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE live_event (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            topic text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX live_event_topic_idx ON live_event (topic, created_at)")
    op.execute("CREATE INDEX live_event_created_idx ON live_event (created_at)")
    for statement in (
        "COMMENT ON TABLE live_event IS 'Outbox of dashboard change notifications: one row "
        "per topic a committed write changed, also sent on pg_notify channel "
        "open_swe_live. Read only to replay what a reconnecting browser missed; rows older "
        "than a day are pruned.'",
        "COMMENT ON COLUMN live_event.topic IS 'What changed, e.g. workspaces or "
        "pr/<uuid>. Carries no data: a browser refetches whatever reads that topic.'",
        "COMMENT ON COLUMN live_event.created_at IS 'Database clock at insert. Replay "
        "windows are durations against this clock, so browser clock skew never matters.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
