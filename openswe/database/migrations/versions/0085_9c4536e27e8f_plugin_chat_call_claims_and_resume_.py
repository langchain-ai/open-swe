"""Plugin chat call claims and resume budget.

``plugin_chat_call.claimed_at`` is when a dock took a call to run in its
plugin's frame, so a second open dock leaves it alone; a claim older than a
few minutes, from a dock that closed mid-call, can be taken again.

``plugin_chat.automatic_resumes`` counts the runs the plugin's results started
since the person last wrote. Past a limit the agent waits for them.
"""

from alembic import op

revision = "9c4536e27e8f"
down_revision = "85c030d23f89"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE plugin_chat_call ADD COLUMN claimed_at timestamptz")
    op.execute("ALTER TABLE plugin_chat ADD COLUMN automatic_resumes integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    raise NotImplementedError
