"""Merge the fork's wake config merge point into upstream's merge queue head."""

revision = "fe95bd992a81"
down_revision = ("37c4ec781c3c", "79a94ddd5d16")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError
