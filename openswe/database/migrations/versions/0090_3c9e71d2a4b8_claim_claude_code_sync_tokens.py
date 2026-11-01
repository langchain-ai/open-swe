"""Claim Claude Code sync tokens.

The ``connect_claude_code`` tool hands out a one-time claim instead of a token, so
the token never passes through an agent. Redeeming the claim mints the token and
records the claim's id, unique so a claim mints only once.
"""

from alembic import op

revision = "3c9e71d2a4b8"
down_revision = "5f101256cbc0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE claude_code_sync_token ADD COLUMN claim_id uuid")
    op.execute(
        "CREATE UNIQUE INDEX claude_code_sync_token_claim ON claude_code_sync_token (claim_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
