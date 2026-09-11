SET search_path TO open_swe, public;

CREATE TABLE IF NOT EXISTS additive_event_projection (
    workspace_id uuid NOT NULL,
    partition_date date NOT NULL,
    event_name text NOT NULL,
    event_count bigint NOT NULL,
    PRIMARY KEY (workspace_id, partition_date, event_name)
);

-- Existing summaries can include events that have already expired.
-- Receipt time distinguishes late ingestion from earlier queue creation.
WITH retained_events AS (
    SELECT e.workspace_id, (e.occurred_at AT TIME ZONE 'UTC')::date AS partition_date,
           e.event_name, COALESCE(r.received_at, e.recorded_at) AS received_at
    FROM events e LEFT JOIN ingestion_receipts r
      ON r.workspace_id = e.workspace_id AND r.producer = e.producer
     AND r.producer_event_id = e.producer_event_id AND r.event_name = e.event_name
     AND r.event_id = e.event_id
), summary_totals AS (
    SELECT s.workspace_id, s.partition_date, counter.key AS event_name,
           counter.value::bigint + count(e.event_name) AS event_count
    FROM daily_summaries s
    CROSS JOIN LATERAL jsonb_each_text(s.counters) counter
    LEFT JOIN retained_events e
      ON e.workspace_id = s.workspace_id AND e.partition_date = s.partition_date
     AND e.event_name = counter.key AND e.received_at > s.recomputed_at
    WHERE s.family = 'additive' AND s.dimension_key = ''
    GROUP BY s.workspace_id, s.summary_version, s.partition_date, counter.key, counter.value
)
INSERT INTO additive_event_projection (workspace_id, partition_date, event_name, event_count)
SELECT workspace_id, partition_date, event_name, max(event_count)
FROM (
    SELECT workspace_id, partition_date, event_name, count(*) AS event_count
    FROM retained_events GROUP BY workspace_id, partition_date, event_name
    UNION ALL
    SELECT workspace_id, partition_date, event_name, event_count FROM summary_totals
) counts
GROUP BY workspace_id, partition_date, event_name
ON CONFLICT (workspace_id, partition_date, event_name) DO UPDATE SET
    event_count = GREATEST(additive_event_projection.event_count, EXCLUDED.event_count);
