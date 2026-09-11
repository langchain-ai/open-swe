SET search_path TO open_swe, public;

ALTER TABLE deployment_metadata
    ADD COLUMN IF NOT EXISTS reporting_cutover_at timestamptz;
