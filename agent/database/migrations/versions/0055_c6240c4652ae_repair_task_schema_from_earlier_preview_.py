"""Repair task schema from earlier preview drafts"""

from alembic import op

revision = "c6240c4652ae"
down_revision = ["6f06bbadfc02", "a823229a905f"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('coordinated_task') IS NOT NULL
               AND to_regclass('task') IS NULL THEN
                ALTER TABLE coordinated_task RENAME TO task;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'task' AND column_name = 'workspace'
            ) THEN
                ALTER TABLE task ADD COLUMN IF NOT EXISTS workspace_id uuid;
                UPDATE task SET workspace_id = workspace.id
                    FROM workspace WHERE workspace.slug = task.workspace;
                ALTER TABLE task ALTER COLUMN workspace_id SET NOT NULL;
                ALTER TABLE task ADD CONSTRAINT task_workspace_id_fkey
                    FOREIGN KEY (workspace_id) REFERENCES workspace(id) ON DELETE CASCADE;
                ALTER TABLE task DROP COLUMN workspace;
            END IF;
            ALTER TABLE task ALTER COLUMN coordinator_thread_id DROP NOT NULL;
            ALTER TABLE task DROP COLUMN IF EXISTS acceptance_criteria;
            ALTER TABLE task DROP COLUMN IF EXISTS status;
            ALTER TABLE task DROP COLUMN IF EXISTS assessment;
            ALTER TABLE task DROP COLUMN IF EXISTS revision;
        END $$
    """)


def downgrade() -> None:
    raise NotImplementedError
