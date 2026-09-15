"""Repair missing PR configured-model attribution from opening runs."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE pr_projection AS pr
        SET originating_model_id = run.configured_model_id,
            model_attribution_quality = 'configured',
            updated_at = clock_timestamp()
        FROM run_projection AS run
        WHERE run.workspace_id = pr.workspace_id
          AND run.run_id = pr.opening_run_id
          AND run.configured_model_id IS NOT NULL
          AND pr.originating_model_id IS NULL
          AND pr.model_attribution_quality = 'unavailable'
        """
    )


def downgrade() -> None:
    raise NotImplementedError
