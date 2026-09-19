"""Retain verified PR distance independently of lifecycle and raw events."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pr_distance_measurements (
            workspace_id uuid NOT NULL,
            pr_id uuid NOT NULL,
            repository_id uuid NOT NULL,
            measurement jsonb NOT NULL,
            event_id uuid NOT NULL,
            measured_at timestamptz NOT NULL,
            recorded_at timestamptz NOT NULL,
            PRIMARY KEY (workspace_id, pr_id),
            CHECK (jsonb_typeof(measurement -> 'distance_basis_points') = 'number'
                AND measurement ? 'distance_basis_points'
                AND (measurement ->> 'distance_basis_points')::integer BETWEEN 0 AND 10000)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
