"""Merge the fork's Notion pending assigner branch into upstream's head."""

revision = "aaf26306180f"
down_revision = ("3a30a333c2e6", "8cb312f3b529")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError
