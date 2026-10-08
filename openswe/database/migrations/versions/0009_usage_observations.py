from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE latest_cost_projection ALTER COLUMN observation_revision TYPE bigint
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS outbox_run_events_idx
            ON outbox (workspace_id, (event_body ->> 'run_id'), (event_body ->> 'event_name'))
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_usage_projection (
            workspace_id uuid NOT NULL,
            pr_id uuid NOT NULL,
            user_id uuid,
            additions integer CHECK (additions >= 0),
            deletions integer CHECK (deletions >= 0),
            changed_files integer CHECK (changed_files >= 0),
            additions_observed_at timestamptz,
            additions_source_version bigint,
            additions_event_id uuid,
            deletions_observed_at timestamptz,
            deletions_source_version bigint,
            deletions_event_id uuid,
            changed_files_observed_at timestamptz,
            changed_files_source_version bigint,
            changed_files_event_id uuid,
            observed_at timestamptz NOT NULL,
            source_version bigint,
            event_id uuid NOT NULL,
            PRIMARY KEY (workspace_id, pr_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finding_usage_projection (
            workspace_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            review_id uuid,
            pr_id uuid NOT NULL,
            repository_id uuid NOT NULL,
            recorded_at timestamptz,
            surfaced_at timestamptz,
            resolved_at timestamptz,
            current_state text NOT NULL CHECK (current_state IN ('open', 'resolved', 'dismissed')),
            severity text NOT NULL,
            category text NOT NULL,
            first_seen_revision_id uuid,
            resolved_revision_id uuid,
            human_replies integer NOT NULL CHECK (human_replies >= 0),
            observed_at timestamptz NOT NULL,
            source_version bigint,
            event_id uuid NOT NULL,
            PRIMARY KEY (workspace_id, finding_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS finding_usage_recorded_idx
            ON finding_usage_projection (workspace_id, recorded_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS finding_usage_surfaced_idx
            ON finding_usage_projection (workspace_id, surfaced_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS review_projection_published_idx
            ON review_projection (workspace_id, published_at)
        """
    )

    op.execute(
        """
        ALTER TABLE identity_directory ADD COLUMN IF NOT EXISTS identity_kind text NOT NULL DEFAULT 'immutable'
            CHECK (identity_kind IN ('immutable', 'provisional'))
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_aliases (
            workspace_id uuid NOT NULL,
            alias_person_id uuid NOT NULL,
            person_id uuid NOT NULL,
            PRIMARY KEY (workspace_id, alias_person_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS identity_aliases_person_idx
            ON identity_aliases (workspace_id, person_id)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_cost_refresh (
            workspace_id uuid NOT NULL,
            run_id uuid NOT NULL,
            scheduled_at timestamptz NOT NULL,
            PRIMARY KEY (workspace_id, run_id)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
