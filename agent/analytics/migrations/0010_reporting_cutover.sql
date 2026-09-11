SET search_path TO open_swe_analytics, public;

ALTER TABLE deployment_metadata
    ADD COLUMN IF NOT EXISTS reporting_cutover_at timestamptz;

INSERT INTO schema_migrations(version) VALUES (10) ON CONFLICT DO NOTHING;
