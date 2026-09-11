SET search_path TO open_swe, public;

-- Finding history must survive raw-event retention: the projection rebuild in
-- ingestion derives resolved/dismissed/reopen history from the events table,
-- which is pruned after ANALYTICS_RAW_EVENT_MONTHS. Snapshot the current
-- durable history into baseline columns and have the rebuild merge on top of
-- them instead of replacing them.
ALTER TABLE finding_projection
    ADD COLUMN IF NOT EXISTS history_baseline_at timestamptz,
    ADD COLUMN IF NOT EXISTS history_baseline_resolved_at timestamptz,
    ADD COLUMN IF NOT EXISTS history_baseline_dismissed_at timestamptz,
    ADD COLUMN IF NOT EXISTS history_baseline_reopened_count integer NOT NULL DEFAULT 0;

UPDATE finding_projection
SET history_baseline_at = clock_timestamp(),
    history_baseline_resolved_at = resolved_at,
    history_baseline_dismissed_at = dismissed_at,
    history_baseline_reopened_count = reopened_count
WHERE resolved_at IS NOT NULL
   OR dismissed_at IS NOT NULL
   OR reopened_count > 0;
