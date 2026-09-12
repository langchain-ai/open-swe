from agent.analytics.migrations.operations import execute_script

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nCREATE TABLE deployment_metadata (\n    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),\n    workspace_id uuid NOT NULL UNIQUE,\n    collection_started_at timestamptz\n);\n\n-- Preserve the namespace of any existing facts, including retained projections.\nWITH workspaces AS (\n    SELECT workspace_id FROM events\n    UNION SELECT workspace_id FROM outbox\n    UNION SELECT workspace_id FROM ingestion_receipts\n    UNION SELECT workspace_id FROM run_projection\n    UNION SELECT workspace_id FROM task_projection\n    UNION SELECT workspace_id FROM pr_projection\n    UNION SELECT workspace_id FROM pr_run_link_projection\n    UNION SELECT workspace_id FROM latest_cost_projection\n    UNION SELECT workspace_id FROM review_projection\n    UNION SELECT workspace_id FROM finding_projection\n    UNION SELECT workspace_id FROM feedback_projection\n    UNION SELECT workspace_id FROM feedback_withdrawal_projection\n    UNION SELECT workspace_id FROM projection_conflicts\n    UNION SELECT workspace_id FROM dirty_summary_partitions\n    UNION SELECT workspace_id FROM daily_summaries\n    UNION SELECT workspace_id FROM additive_event_projection\n    UNION SELECT workspace_id FROM identity_directory\n    UNION SELECT workspace_id FROM model_directory\n    UNION SELECT workspace_id FROM repository_directory\n    UNION SELECT workspace_id FROM named_export_audit\n), captures AS (\n    SELECT min(recorded_at) AS captured_at FROM events\n    UNION ALL SELECT min(created_at) FROM outbox\n    UNION ALL SELECT min(received_at) FROM ingestion_receipts\n)\nINSERT INTO deployment_metadata (workspace_id, collection_started_at)\nVALUES (\n    COALESCE((SELECT workspace_id FROM workspaces), gen_random_uuid()),\n    (SELECT min(captured_at) FROM captures)\n);\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
