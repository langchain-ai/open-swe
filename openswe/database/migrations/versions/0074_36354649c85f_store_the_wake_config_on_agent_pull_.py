"""Store the configurable that wakes the agent thread which opened a pull request.

Lets the reviewer hand its findings back to that thread as the source it
started from (Slack, Linear, Notion, GitHub issue). Links made before this, and
links for threads that did not open the PR, stay NULL.
"""

from alembic import op

revision = "36354649c85f"
down_revision = "aaf26306180f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pull_request_thread ADD COLUMN wake_config jsonb")


def downgrade() -> None:
    raise NotImplementedError
