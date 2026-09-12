from agent.analytics.migrations.operations import execute_script

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nALTER TABLE pr_projection ADD COLUMN latest_transition_at timestamptz;\n\nUPDATE pr_projection p SET latest_transition_at = COALESCE(\n    (SELECT max(e.occurred_at) FROM events e\n     WHERE e.workspace_id = p.workspace_id AND e.pr_id = p.pr_id\n       AND e.event_name IN ('pr.merged', 'pr.closed_without_merge', 'pr.reopened')\n       AND e.source_version IS NOT DISTINCT FROM p.source_version),\n    p.outcome_at, p.opened_at\n);\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
