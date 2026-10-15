"""Drop LangGraph Store copy markers and import progress.

Every Store import has finished, so nothing reads ``copied_from_store`` (the
columns, or the key MCP connections carried in their JSON) or ``store_import``.
"""

from alembic import op

revision = "d80046823007"
down_revision = "e5e52ce891f4"
branch_labels = None
depends_on = None

_MARKED_TABLES = (
    "allowed_slack_bot",
    "incident_command",
    "mcp_oauth_entry",
    "reviewer_eval_run",
    "slack_message",
    "slack_run",
    "slack_thread",
    "slack_view_suggestion",
)


def upgrade() -> None:
    for table in _MARKED_TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN copied_from_store")
    op.execute("""
        UPDATE mcp_connection SET connection = connection - 'copied_from_store'
        WHERE connection ? 'copied_from_store'
    """)
    op.execute("""
        UPDATE user_record SET value = value - 'copied_from_store'
        WHERE kind = 'mcp_connection' AND value ? 'copied_from_store'
    """)
    op.execute("DROP TABLE store_import")


def downgrade() -> None:
    raise NotImplementedError
