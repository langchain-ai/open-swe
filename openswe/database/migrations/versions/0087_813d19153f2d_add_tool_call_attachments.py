"""Images a tool call returned, such as a browser screenshot, as attachment metadata."""

from alembic import op

revision = "813d19153f2d"
down_revision = "0497c835bcfc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: a preview build of the pull request may already have added it.
    op.execute("ALTER TABLE thread_tool_call ADD COLUMN IF NOT EXISTS attachments jsonb")


def downgrade() -> None:
    raise NotImplementedError
