"""Drop sandbox tool context.

It held the run config the sandbox tools endpoint rebuilt a thread's tools from;
code mode replaced that endpoint.
"""

from alembic import op

revision = "87e4182be853"
down_revision = "3bed5f27edb1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_tool_context")


def downgrade() -> None:
    raise NotImplementedError
