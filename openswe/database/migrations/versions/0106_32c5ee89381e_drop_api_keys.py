"""Drop Robin's own API keys.

Callers authenticate with LangSmith API keys instead (a personal access token is
its person, a service key is the workspace's bot), so nothing reads
``api_key`` any more. No key was in use, and the hashes go with the table.
"""

from alembic import op

revision = "32c5ee89381e"
down_revision = "3d2a1cb2c1bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_key")


def downgrade() -> None:
    raise NotImplementedError
