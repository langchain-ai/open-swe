"""Event log"""

from alembic import op

revision = "ec8eaeed6158"
down_revision = "8114633625db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE event_log (
            received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            source text NOT NULL,
            endpoint text NOT NULL,
            event_type text NOT NULL DEFAULT '',
            delivery_id text NOT NULL DEFAULT '',
            payload jsonb NOT NULL,
            user_id uuid,
            workspace_id uuid,
            repository_id uuid,
            pull_request_id uuid
        ) PARTITION BY RANGE (received_at)
        """
    )
    for column in ("user_id", "workspace_id", "repository_id", "pull_request_id"):
        op.execute(
            f"CREATE INDEX event_log_{column.removesuffix('_id')}_idx ON event_log ({column}) "
            f"WHERE {column} IS NOT NULL"
        )
    op.execute("CREATE INDEX event_log_delivery_idx ON event_log (source, delivery_id)")
    op.execute("CREATE INDEX event_log_received_idx ON event_log (received_at, source, event_type)")
    for statement in (
        "COMMENT ON TABLE event_log IS 'Append-only log of every signature-verified inbound "
        "webhook. One partition per UTC day (event_log_YYYYMMDD); EventLog.ensure_partitions() "
        "creates today''s and tomorrow''s and drops everything older than yesterday.'",
        "COMMENT ON COLUMN event_log.received_at IS 'Database clock at insert; the partition key.'",
        "COMMENT ON COLUMN event_log.source IS 'Sending service: github, slack, linear.'",
        "COMMENT ON COLUMN event_log.endpoint IS 'Request path the delivery arrived on, e.g. "
        "/webhooks/slack/interactivity.'",
        "COMMENT ON COLUMN event_log.event_type IS 'GitHub: X-GitHub-Event. Linear: Linear-Event. "
        "Slack: inner event type, slash command, or interaction type.'",
        "COMMENT ON COLUMN event_log.delivery_id IS 'GitHub: X-GitHub-Delivery. Linear: "
        "Linear-Delivery. Slack: event_id, or trigger_id for commands and interactions.'",
        "COMMENT ON COLUMN event_log.payload IS 'Request body as sent. Form-encoded bodies are "
        "stored as an object of their fields; Slack interactivity keeps its JSON in the payload "
        "field as a string.'",
        "COMMENT ON COLUMN event_log.user_id IS 'users.id of the sender (GitHub sender, Slack "
        "user, Linear actor email), resolved at insert. No foreign key, so deletes elsewhere "
        "never touch the log.'",
        "COMMENT ON COLUMN event_log.workspace_id IS 'workspace.id owning the repository or "
        "Slack channel, resolved at insert.'",
        "COMMENT ON COLUMN event_log.repository_id IS 'repository.id of a GitHub delivery, "
        "resolved at insert.'",
        "COMMENT ON COLUMN event_log.pull_request_id IS 'pull_request.id of a GitHub pull "
        "request, review, or PR comment delivery, resolved at insert.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE event_log")
