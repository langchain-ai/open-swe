"""Mark system automations"""

from alembic import op

revision = "1a685fe0033d"
down_revision = ["cff3e5d43f43", "147a16ec2b4a", "c04ec5f250f1"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A system automation belongs to the environment, not to the person who made it. Only
    # admins could create automations until now, so every existing one is a system one.
    # The shared preview forgets this revision when the PR leaves it and replays it when the
    # PR returns, over the column it already added.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                AND table_name = 'automation' AND column_name = 'system'
            ) THEN
                ALTER TABLE automation ADD COLUMN system boolean NOT NULL DEFAULT false;
                UPDATE automation SET system = true;
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise NotImplementedError
