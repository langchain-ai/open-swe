"""Merge repository usage migration heads"""

revision = "c8ddc8eb4ea6"
down_revision = ["12b3a8ca6a0c", "c8e1ce5e9cbe", "61e8a2c88c54", "d2f506bc3072"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise NotImplementedError
