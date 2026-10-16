"""Seed the default workspace"""

from alembic import op

revision = "ef05fbc2c85a"
down_revision = "ec8eaeed6158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unrouted work lands in `default`, so it must exist for pickers to offer it.
    op.execute(
        """
        INSERT INTO workspace (id, slug, name, created_by)
        VALUES (gen_random_uuid(), 'default', 'Default', 'open-swe')
        ON CONFLICT (slug) DO NOTHING
        """
    )


def downgrade() -> None:
    raise NotImplementedError
