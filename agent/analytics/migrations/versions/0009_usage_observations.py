from agent.analytics.migrations.operations import execute_script

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nALTER TABLE latest_cost_projection ALTER COLUMN observation_revision TYPE bigint;\nCREATE INDEX IF NOT EXISTS outbox_run_events_idx\n    ON outbox (workspace_id, (event_body ->> 'run_id'), (event_body ->> 'event_name'));\n\nCREATE TABLE IF NOT EXISTS pr_usage_projection (\n    workspace_id uuid NOT NULL,\n    pr_id uuid NOT NULL,\n    user_id uuid,\n    additions integer CHECK (additions >= 0),\n    deletions integer CHECK (deletions >= 0),\n    changed_files integer CHECK (changed_files >= 0),\n    additions_observed_at timestamptz,\n    additions_source_version bigint,\n    additions_event_id uuid,\n    deletions_observed_at timestamptz,\n    deletions_source_version bigint,\n    deletions_event_id uuid,\n    changed_files_observed_at timestamptz,\n    changed_files_source_version bigint,\n    changed_files_event_id uuid,\n    observed_at timestamptz NOT NULL,\n    source_version bigint,\n    event_id uuid NOT NULL,\n    PRIMARY KEY (workspace_id, pr_id)\n);\n\nCREATE TABLE IF NOT EXISTS finding_usage_projection (\n    workspace_id uuid NOT NULL,\n    finding_id uuid NOT NULL,\n    review_id uuid,\n    pr_id uuid NOT NULL,\n    repository_id uuid NOT NULL,\n    recorded_at timestamptz,\n    surfaced_at timestamptz,\n    resolved_at timestamptz,\n    current_state text NOT NULL CHECK (current_state IN ('open', 'resolved', 'dismissed')),\n    severity text NOT NULL,\n    category text NOT NULL,\n    first_seen_revision_id uuid,\n    resolved_revision_id uuid,\n    human_replies integer NOT NULL CHECK (human_replies >= 0),\n    observed_at timestamptz NOT NULL,\n    source_version bigint,\n    event_id uuid NOT NULL,\n    PRIMARY KEY (workspace_id, finding_id)\n);\nCREATE INDEX IF NOT EXISTS finding_usage_recorded_idx\n    ON finding_usage_projection (workspace_id, recorded_at);\nCREATE INDEX IF NOT EXISTS finding_usage_surfaced_idx\n    ON finding_usage_projection (workspace_id, surfaced_at);\n\nCREATE INDEX IF NOT EXISTS review_projection_published_idx\n    ON review_projection (workspace_id, published_at);\n\nALTER TABLE identity_directory ADD COLUMN IF NOT EXISTS identity_kind text NOT NULL DEFAULT 'immutable'\n    CHECK (identity_kind IN ('immutable', 'provisional'));\n\nCREATE TABLE IF NOT EXISTS identity_aliases (\n    workspace_id uuid NOT NULL,\n    alias_person_id uuid NOT NULL,\n    person_id uuid NOT NULL,\n    PRIMARY KEY (workspace_id, alias_person_id)\n);\nCREATE INDEX IF NOT EXISTS identity_aliases_person_idx\n    ON identity_aliases (workspace_id, person_id);\n\nCREATE TABLE IF NOT EXISTS run_cost_refresh (\n    workspace_id uuid NOT NULL,\n    run_id uuid NOT NULL,\n    scheduled_at timestamptz NOT NULL,\n    PRIMARY KEY (workspace_id, run_id)\n);\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
