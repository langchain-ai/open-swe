"""Index event log by source and event type"""

from alembic import op

revision = "37e4a81fa83f"
down_revision = ["3a061d17ed2a", "3cce7935a681", "1c56fc371df9", "31f7578d4024"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("CREATE INDEX event_log_kind_idx ON event_log (source, event_type, received_at)")
    op.execute("DROP INDEX event_log_received_idx")
    op.execute(
        "COMMENT ON COLUMN event_log.event_type IS 'GitHub: X-GitHub-Event. Linear: Linear-Event. "
        "Slack: inner event type, slash command, or interaction type. Suffixed with "
        ".<action> when the payload has a string action, e.g. pull_request.opened.'"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("CREATE INDEX event_log_received_idx ON event_log (received_at, source, event_type)")
    op.execute("DROP INDEX event_log_kind_idx")
    op.execute(
        "COMMENT ON COLUMN event_log.event_type IS 'GitHub: X-GitHub-Event. Linear: Linear-Event. "
        "Slack: inner event type, slash command, or interaction type.'"
    )
