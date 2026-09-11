SET search_path TO open_swe, public;

ALTER TABLE pr_projection ALTER COLUMN opening_run_id DROP NOT NULL;
