SET search_path TO open_swe_analytics, public;

CREATE TABLE IF NOT EXISTS additive_event_projection (
    workspace_id uuid NOT NULL,
    partition_date date NOT NULL,
    event_name text NOT NULL,
    event_count bigint NOT NULL,
    PRIMARY KEY (workspace_id, partition_date, event_name)
);

-- Existing summaries can include events that have already expired.
INSERT INTO additive_event_projection (workspace_id, partition_date, event_name, event_count)
SELECT workspace_id, partition_date, event_name, max(event_count)
FROM (
    SELECT workspace_id, (occurred_at AT TIME ZONE 'UTC')::date AS partition_date,
           event_name, count(*) AS event_count
    FROM events GROUP BY workspace_id, partition_date, event_name
    UNION ALL
    SELECT workspace_id, partition_date, counter.key, counter.value::bigint
    FROM daily_summaries CROSS JOIN LATERAL jsonb_each_text(counters) counter
    WHERE family = 'additive' AND dimension_key = ''
) counts
GROUP BY workspace_id, partition_date, event_name
ON CONFLICT (workspace_id, partition_date, event_name) DO UPDATE SET
    event_count = GREATEST(additive_event_projection.event_count, EXCLUDED.event_count);
