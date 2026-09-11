SET search_path TO open_swe_analytics, public;

ALTER TABLE pr_projection ADD COLUMN latest_transition_at timestamptz;

UPDATE pr_projection p SET latest_transition_at = COALESCE(
    (SELECT max(e.occurred_at) FROM events e
     WHERE e.workspace_id = p.workspace_id AND e.pr_id = p.pr_id
       AND e.event_name IN ('pr.merged', 'pr.closed_without_merge', 'pr.reopened')
       AND e.source_version IS NOT DISTINCT FROM p.source_version),
    p.outcome_at, p.opened_at
);
