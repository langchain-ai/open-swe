"""Merge the fork's agent pull wake config branch into upstream's head."""

revision = "37c4ec781c3c"
down_revision = ("36354649c85f", "87e4182be853")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError
