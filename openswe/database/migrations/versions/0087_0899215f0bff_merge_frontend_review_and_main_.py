"""Merge frontend review and main migration heads"""

revision = "0899215f0bff"
down_revision = ["393bdf1b96e5", "0497c835bcfc"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError
