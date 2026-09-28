"""Event subscriptions"""

from alembic import op

revision = "c8e1ce5e9cbe"
down_revision = "b652546da9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_subscription (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            source text NOT NULL CHECK (source IN ('github')),
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            event_types text[] NOT NULL DEFAULT '{}',
            actions text[] NOT NULL DEFAULT '{}',
            multitask_strategy text NOT NULL
                CHECK (multitask_strategy IN ('enqueue', 'interrupt')),
            instructions text NOT NULL DEFAULT '',
            one_shot boolean NOT NULL DEFAULT false,
            run_config jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            trigger_count integer NOT NULL DEFAULT 0,
            last_triggered_at timestamptz
        )
        """
    )
    op.execute(
        "CREATE INDEX event_subscription_pull_request_idx ON event_subscription (pull_request_id)"
    )
    op.execute("CREATE INDEX event_subscription_thread_idx ON event_subscription (thread_id)")
    for statement in (
        "COMMENT ON TABLE event_subscription IS 'An agent thread listening for event_log rows; "
        "each matching row starts a run on the thread.'",
        "COMMENT ON COLUMN event_subscription.event_types IS 'event_log.event_type values to "
        "match; empty matches every type.'",
        "COMMENT ON COLUMN event_subscription.actions IS 'Payload action values to match, e.g. "
        "completed or submitted; empty matches every action.'",
        "COMMENT ON COLUMN event_subscription.run_config IS 'The configurable each woken run "
        "starts with.'",
        "COMMENT ON COLUMN event_subscription.one_shot IS 'Deleted after its first successful "
        "wake.'",
        "COMMENT ON COLUMN event_subscription.trigger_count IS 'Runs this subscription has "
        "started.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE event_subscription")
