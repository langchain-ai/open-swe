"""Drop expedited card diff image"""

from alembic import op

revision = "45ca3f99680e"
down_revision = "b652546da9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE expedited_approval DROP COLUMN slack_diff_file_id")


def downgrade() -> None:
    raise NotImplementedError
