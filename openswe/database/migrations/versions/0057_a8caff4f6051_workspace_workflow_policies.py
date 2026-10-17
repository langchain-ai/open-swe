"""Workspace workflow policies"""

from alembic import op

revision = "a8caff4f6051"
down_revision = ["9ec28aa0b2a8", "b438344a06b7", "da651a8f67cc"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace_workflow_policy (
            repository_id uuid NOT NULL
                REFERENCES workspace_repository (repository_id) ON DELETE CASCADE,
            workflow text NOT NULL,
            ref text NOT NULL CHECK (ref <> ''),
            fixed_inputs jsonb NOT NULL DEFAULT '{}'::jsonb,
            allow_bot_inputs boolean NOT NULL DEFAULT false,
            admin_only boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (repository_id, workflow)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
