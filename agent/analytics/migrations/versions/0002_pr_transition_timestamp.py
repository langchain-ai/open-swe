from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pr_projection ADD COLUMN latest_transition_at timestamptz
        """
    )

    op.execute(
        """
        UPDATE pr_projection p SET latest_transition_at = COALESCE(
            (SELECT max(e.occurred_at) FROM events e
             WHERE e.workspace_id = p.workspace_id AND e.pr_id = p.pr_id
               AND e.event_name IN ('pr.merged', 'pr.closed_without_merge', 'pr.reopened')
               AND e.source_version IS NOT DISTINCT FROM p.source_version),
            p.outcome_at, p.opened_at
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
