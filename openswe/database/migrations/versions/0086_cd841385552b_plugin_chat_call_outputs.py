"""Plugin chat call outputs.

``plugin_chat_call.output`` is what the plugin's frame returned for a call, and
``delivered_at`` when a run took it to the agent. A call answered after its
chat's automatic resumes ran out waits, undelivered, for the person's next
message, which carries it to the agent.
"""

from alembic import op

revision = "cd841385552b"
down_revision = "9c4536e27e8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every statement can run again: a preview database may keep these tables after
    # forgetting it stamped them, when its branch left the preview tree for a while.
    op.execute("ALTER TABLE plugin_chat_call ADD COLUMN IF NOT EXISTS output text")
    op.execute("ALTER TABLE plugin_chat_call ADD COLUMN IF NOT EXISTS delivered_at timestamptz")


def downgrade() -> None:
    raise NotImplementedError
