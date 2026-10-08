"""Event subscriptions"""

from alembic import op

revision = "c8e1ce5e9cbe"
down_revision = "84e4e97b6fec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_subscription (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
            sources text[] NOT NULL DEFAULT '{}',
            repository_id uuid REFERENCES repository (id) ON DELETE CASCADE,
            pull_request_id uuid REFERENCES pull_request (id) ON DELETE CASCADE,
            event_types text[] NOT NULL DEFAULT '{}',
            payload_match jsonb NOT NULL DEFAULT '{}',
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
    op.execute("CREATE INDEX event_subscription_workspace_idx ON event_subscription (workspace_id)")
    op.execute("CREATE INDEX event_subscription_thread_idx ON event_subscription (thread_id)")
    op.execute(
        """
        CREATE TABLE event_match (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            subscription_id uuid NOT NULL,
            source text NOT NULL,
            delivery_id text NOT NULL,
            content text NOT NULL,
            run_config jsonb NOT NULL,
            delivery_attempts integer NOT NULL DEFAULT 0,
            matched_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX event_match_thread_idx ON event_match (thread_id, matched_at)")
    op.execute(
        "CREATE UNIQUE INDEX event_match_delivery_idx ON event_match "
        "(thread_id, source, delivery_id) WHERE delivery_id <> ''"
    )
    for statement in (
        "COMMENT ON TABLE event_subscription IS 'An agent thread listening for event_log rows; "
        "each matching row is recorded in event_match for the thread.'",
        "COMMENT ON TABLE event_match IS 'Events owed to a thread. A match is delivered once a "
        "message in the thread state carries its id; older than two days are dropped.'",
        "COMMENT ON COLUMN event_match.subscription_id IS 'The event_subscription that matched; "
        "no foreign key, since a one-shot is deleted as it matches.'",
        "COMMENT ON COLUMN event_match.content IS 'The wake message, rendered at match time.'",
        "COMMENT ON COLUMN event_match.delivery_attempts IS 'Runs started to deliver it. After "
        "three, only a run started for another reason delivers it.'",
        "COMMENT ON COLUMN event_subscription.workspace_id IS 'Matches event_log rows with this "
        "workspace_id: the subscribing thread''s workspace.'",
        "COMMENT ON COLUMN event_subscription.sources IS 'event_log.source values to match; "
        "empty matches every source.'",
        "COMMENT ON COLUMN event_subscription.repository_id IS 'When set, only rows for this "
        "repository match.'",
        "COMMENT ON COLUMN event_subscription.pull_request_id IS 'When set, only rows for this "
        "pull request match.'",
        "COMMENT ON COLUMN event_subscription.event_types IS 'event_log.event_type values to "
        "match; empty matches every type.'",
        "COMMENT ON COLUMN event_subscription.payload_match IS 'A JSON object the event_log "
        "payload must contain (jsonb @>); the empty object matches every payload.'",
        "COMMENT ON COLUMN event_subscription.run_config IS 'The configurable each woken run "
        "starts with.'",
        "COMMENT ON COLUMN event_subscription.one_shot IS 'Deleted after its first match.'",
        "COMMENT ON COLUMN event_subscription.trigger_count IS 'Events this subscription has "
        "matched.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE event_match")
    op.execute("DROP TABLE event_subscription")
