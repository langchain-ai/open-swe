"""Enable fuzzy search"""

from alembic import op

revision = "bafee27672d1"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")


def downgrade() -> None:
    raise NotImplementedError
