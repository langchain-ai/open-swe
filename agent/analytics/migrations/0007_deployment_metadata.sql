SET search_path TO open_swe_analytics, public;

CREATE TABLE deployment_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    workspace_id uuid NOT NULL UNIQUE,
    collection_started_at timestamptz
);

-- Preserve the namespace of any existing facts, including retained projections.
WITH workspaces AS (
    SELECT workspace_id FROM events
    UNION SELECT workspace_id FROM outbox
    UNION SELECT workspace_id FROM ingestion_receipts
    UNION SELECT workspace_id FROM run_projection
    UNION SELECT workspace_id FROM task_projection
    UNION SELECT workspace_id FROM pr_projection
    UNION SELECT workspace_id FROM pr_run_link_projection
    UNION SELECT workspace_id FROM latest_cost_projection
    UNION SELECT workspace_id FROM review_projection
    UNION SELECT workspace_id FROM finding_projection
    UNION SELECT workspace_id FROM feedback_projection
    UNION SELECT workspace_id FROM feedback_withdrawal_projection
    UNION SELECT workspace_id FROM projection_conflicts
    UNION SELECT workspace_id FROM dirty_summary_partitions
    UNION SELECT workspace_id FROM daily_summaries
    UNION SELECT workspace_id FROM additive_event_projection
    UNION SELECT workspace_id FROM identity_directory
    UNION SELECT workspace_id FROM model_directory
    UNION SELECT workspace_id FROM repository_directory
    UNION SELECT workspace_id FROM named_export_audit
), captures AS (
    SELECT min(recorded_at) AS captured_at FROM events
    UNION ALL SELECT min(created_at) FROM outbox
    UNION ALL SELECT min(received_at) FROM ingestion_receipts
)
INSERT INTO deployment_metadata (workspace_id, collection_started_at)
VALUES (
    COALESCE((SELECT workspace_id FROM workspaces), gen_random_uuid()),
    (SELECT min(captured_at) FROM captures)
);
