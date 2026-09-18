"""Remove the historical PR opener contradicted by its invocation chronology."""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH contradicted AS MATERIALIZED (
            SELECT pr.workspace_id, pr.pr_id, run.run_id
            FROM pr_projection AS pr
            JOIN run_projection AS run ON run.workspace_id = pr.workspace_id
            WHERE pr.workspace_id = '7849c27e-81ef-4651-8982-719c84d95e7d'::uuid
              AND pr.pr_id = 'aba01c5a-66a4-5b57-97f2-1db4d93de929'::uuid
              AND run.run_id = '455db9e6-ff8d-5aea-baed-72b11b609348'::uuid
              AND run.started_at > pr.opened_at + interval '24 hours'
        ), cleared AS (
            UPDATE pr_projection AS pr
            SET opening_run_id = NULL,
                originating_model_id = NULL,
                model_attribution_quality = 'unavailable',
                updated_at = clock_timestamp()
            FROM contradicted AS target
            WHERE pr.workspace_id = target.workspace_id
              AND pr.pr_id = target.pr_id
              AND pr.opening_run_id = target.run_id
        )
        DELETE FROM pr_run_link_projection AS link
        USING contradicted AS target
        WHERE link.workspace_id = target.workspace_id
          AND link.pr_id = target.pr_id
          AND link.run_id = target.run_id
          AND link.link_role = 'opening'
        """
    )


def downgrade() -> None:
    raise NotImplementedError
