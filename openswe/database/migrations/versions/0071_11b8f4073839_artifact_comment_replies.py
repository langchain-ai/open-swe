"""Artifact comment replies"""

from alembic import op

revision = "11b8f4073839"
down_revision = "9f867d4517fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the preview database already has both from a PR build that ran this
    # migration before it merged, and re-adding them stopped every preview revision.
    op.execute("ALTER TABLE thread_plan_comment ADD COLUMN IF NOT EXISTS parent_id text")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'thread_plan_comment_parent_fk'
            ) THEN
                ALTER TABLE thread_plan_comment
                    ADD CONSTRAINT thread_plan_comment_parent_fk
                    FOREIGN KEY (thread_id, parent_id)
                    REFERENCES thread_plan_comment (thread_id, id)
                    ON DELETE SET NULL (parent_id);
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise NotImplementedError
