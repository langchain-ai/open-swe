SET search_path TO open_swe_analytics, public;

CREATE TABLE feedback_withdrawal_projection (
    workspace_id uuid NOT NULL,
    feedback_id uuid NOT NULL,
    withdrawn_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, feedback_id)
);

INSERT INTO feedback_withdrawal_projection (workspace_id, feedback_id, withdrawn_at)
SELECT workspace_id, (payload->>'submission_event_id')::uuid, min(occurred_at)
FROM events WHERE event_name = 'user.feedback_withdrawn'
GROUP BY workspace_id, (payload->>'submission_event_id')::uuid;

UPDATE feedback_projection f SET withdrawn_at = LEAST(f.withdrawn_at, w.withdrawn_at)
FROM feedback_withdrawal_projection w
WHERE f.workspace_id = w.workspace_id AND f.feedback_id = w.feedback_id;
