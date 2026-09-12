from agent.analytics.migrations.operations import execute_script

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nCREATE TABLE feedback_withdrawal_projection (\n    workspace_id uuid NOT NULL,\n    feedback_id uuid NOT NULL,\n    withdrawn_at timestamptz NOT NULL,\n    PRIMARY KEY (workspace_id, feedback_id)\n);\n\nINSERT INTO feedback_withdrawal_projection (workspace_id, feedback_id, withdrawn_at)\nSELECT workspace_id, (payload->>'submission_event_id')::uuid, min(occurred_at)\nFROM events WHERE event_name = 'user.feedback_withdrawn'\nGROUP BY workspace_id, (payload->>'submission_event_id')::uuid;\n\nUPDATE feedback_projection f SET withdrawn_at = LEAST(f.withdrawn_at, w.withdrawn_at)\nFROM feedback_withdrawal_projection w\nWHERE f.workspace_id = w.workspace_id AND f.feedback_id = w.feedback_id;\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
