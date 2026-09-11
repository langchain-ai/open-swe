SET search_path TO open_swe_analytics, public;

ALTER TABLE deployment_metadata ADD COLUMN last_processed_at timestamptz;

UPDATE deployment_metadata SET last_processed_at = (
    SELECT max(received_at) FROM ingestion_receipts
    WHERE workspace_id = deployment_metadata.workspace_id
);
