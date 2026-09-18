"""Track the trusted source of each analytics display name."""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_directory
            ADD COLUMN IF NOT EXISTS display_name_source text
                CHECK (display_name_source IN ('github', 'slack'))
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'identity_directory_display_name_present_check'
                    AND conrelid = 'identity_directory'::regclass
            ) THEN
                ALTER TABLE identity_directory
                    ADD CONSTRAINT identity_directory_display_name_present_check
                    CHECK (
                        display_name_source IS NULL
                        OR NULLIF(btrim(display_name), '') IS NOT NULL
                    );
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise NotImplementedError
