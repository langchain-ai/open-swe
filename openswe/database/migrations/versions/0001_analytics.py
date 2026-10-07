from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event_ids (
            event_id uuid PRIMARY KEY,
            occurred_at timestamptz NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id uuid NOT NULL,
            event_name text NOT NULL,
            schema_version integer NOT NULL,
            occurred_at timestamptz NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            workspace_id uuid NOT NULL,
            environment text NOT NULL,
            producer text NOT NULL,
            producer_event_id text NOT NULL,
            source_version bigint,
            correlation_id uuid,
            causation_id uuid,
            run_id uuid,
            preparation_run_id uuid,
            thread_id uuid,
            task_id uuid,
            pr_id uuid,
            review_id uuid,
            finding_id uuid,
            user_id uuid,
            team_id uuid,
            repository_id uuid,
            model_id uuid,
            entry_point text NOT NULL,
            privacy_classification text NOT NULL CHECK (privacy_classification IN ('non_personal', 'pseudonymous')),
            payload jsonb NOT NULL,
            PRIMARY KEY (event_id, occurred_at),
            UNIQUE (workspace_id, producer, producer_event_id, event_name, schema_version, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS events_default PARTITION OF events DEFAULT
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS events_workspace_time_idx ON events (workspace_id, occurred_at DESC)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS events_workspace_run_idx ON events (workspace_id, run_id, occurred_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS events_workspace_pr_idx ON events (workspace_id, pr_id, occurred_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS events_workspace_finding_idx ON events (workspace_id, finding_id, occurred_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS events_workspace_user_idx ON events (workspace_id, user_id, occurred_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS events_workspace_repo_idx ON events (workspace_id, repository_id, occurred_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_receipts (
            workspace_id uuid NOT NULL,
            producer text NOT NULL,
            producer_event_id text NOT NULL,
            event_name text NOT NULL,
            event_id uuid NOT NULL,
            received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (workspace_id, producer, producer_event_id, event_name)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingestion_receipts_expiry_idx ON ingestion_receipts (expires_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            event_id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            event_body jsonb NOT NULL,
            state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'delivering', 'acknowledged', 'dead_letter')),
            attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            locked_at timestamptz,
            acknowledged_at timestamptz,
            dead_lettered_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS outbox_delivery_idx ON outbox (state, next_attempt_at, created_at)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS outbox_stale_idx ON outbox (state, created_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_projection (
            workspace_id uuid NOT NULL,
            run_id uuid NOT NULL,
            preparation_run_id uuid,
            thread_id uuid,
            task_id uuid,
            user_id uuid,
            team_id uuid,
            repository_id uuid,
            configured_model_id uuid,
            effective_model_id uuid,
            model_attribution_quality text NOT NULL DEFAULT 'unavailable',
            entry_point text NOT NULL DEFAULT 'unknown',
            started_at timestamptz,
            terminal_at timestamptz,
            technical_status text NOT NULL DEFAULT 'pending' CHECK (technical_status IN ('pending', 'completed', 'failed', 'canceled', 'conflicted')),
            terminal_conflict boolean NOT NULL DEFAULT false,
            terminal_event_ids uuid[] NOT NULL DEFAULT '{}',
            input_tokens bigint,
            output_tokens bigint,
            total_tokens bigint,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, run_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS run_projection_leaderboard_idx ON run_projection (workspace_id, started_at DESC, user_id)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS run_projection_dimensions_idx ON run_projection (workspace_id, team_id, entry_point, effective_model_id, repository_id, started_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS task_projection (
            workspace_id uuid NOT NULL,
            task_id uuid NOT NULL,
            thread_id uuid,
            user_id uuid,
            marked_complete_at timestamptz,
            accepted_at timestamptz,
            major_rework_count integer NOT NULL DEFAULT 0,
            minor_rework_count integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, task_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_projection (
            workspace_id uuid NOT NULL,
            pr_id uuid NOT NULL,
            repository_id uuid NOT NULL,
            opening_run_id uuid NOT NULL,
            originating_model_id uuid,
            model_attribution_quality text NOT NULL,
            opened_at timestamptz NOT NULL,
            current_state text NOT NULL CHECK (current_state IN ('open', 'merged', 'closed_without_merge')),
            outcome_at timestamptz,
            source_version bigint,
            repository_private boolean,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, pr_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS pr_projection_cohort_idx ON pr_projection (workspace_id, opened_at, originating_model_id, current_state)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS pr_projection_repo_idx ON pr_projection (workspace_id, repository_id, opened_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_run_link_projection (
            workspace_id uuid NOT NULL,
            pr_id uuid NOT NULL,
            run_id uuid NOT NULL,
            link_role text NOT NULL,
            linked_at timestamptz NOT NULL,
            PRIMARY KEY (workspace_id, pr_id, run_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_cost_projection (
            workspace_id uuid NOT NULL,
            run_id uuid NOT NULL,
            observation_revision integer NOT NULL,
            observed_at timestamptz NOT NULL,
            status text NOT NULL CHECK (status IN ('complete', 'partial', 'unavailable')),
            cost_usd numeric(20, 8),
            input_tokens bigint,
            output_tokens bigint,
            total_tokens bigint,
            source text NOT NULL,
            missing_reason text,
            event_id uuid NOT NULL,
            PRIMARY KEY (workspace_id, run_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS review_projection (
            workspace_id uuid NOT NULL,
            review_id uuid NOT NULL,
            pr_id uuid,
            repository_id uuid,
            published_at timestamptz NOT NULL,
            finding_count integer NOT NULL,
            PRIMARY KEY (workspace_id, review_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finding_projection (
            workspace_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            review_id uuid,
            pr_id uuid,
            repository_id uuid,
            severity text,
            category text,
            surfaced_at timestamptz,
            current_state text NOT NULL DEFAULT 'open' CHECK (current_state IN ('open', 'resolved', 'dismissed')),
            resolved_at timestamptz,
            dismissed_at timestamptz,
            reopened_count integer NOT NULL DEFAULT 0,
            source_version bigint,
            latest_occurred_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, finding_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS finding_projection_cohort_idx ON finding_projection (workspace_id, surfaced_at, current_state)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_projection (
            workspace_id uuid NOT NULL,
            feedback_id uuid NOT NULL,
            run_id uuid,
            task_id uuid,
            user_id uuid,
            sentiment text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
            rating integer,
            submitted_at timestamptz NOT NULL,
            withdrawn_at timestamptz,
            PRIMARY KEY (workspace_id, feedback_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projection_conflicts (
            workspace_id uuid NOT NULL,
            subject_type text NOT NULL,
            subject_id uuid NOT NULL,
            conflict_type text NOT NULL,
            event_ids uuid[] NOT NULL,
            detected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            resolved_at timestamptz,
            PRIMARY KEY (workspace_id, subject_type, subject_id, conflict_type)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dirty_summary_partitions (
            workspace_id uuid NOT NULL,
            summary_version integer NOT NULL,
            family text NOT NULL,
            partition_date date NOT NULL,
            dimension_key text NOT NULL DEFAULT '',
            dirty_since timestamptz NOT NULL DEFAULT clock_timestamp(),
            reason_event_id uuid NOT NULL,
            PRIMARY KEY (workspace_id, summary_version, family, partition_date, dimension_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_summaries (
            workspace_id uuid NOT NULL,
            summary_version integer NOT NULL,
            family text NOT NULL,
            partition_date date NOT NULL,
            dimension_key text NOT NULL DEFAULT '',
            counters jsonb NOT NULL DEFAULT '{}',
            sums jsonb NOT NULL DEFAULT '{}',
            exact_members uuid[] NOT NULL DEFAULT '{}',
            histogram_bounds bigint[] NOT NULL DEFAULT '{}',
            histogram_counts bigint[] NOT NULL DEFAULT '{}',
            completeness jsonb NOT NULL DEFAULT '{}',
            data_watermark timestamptz,
            recomputed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, summary_version, family, partition_date, dimension_key)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS daily_summaries_query_idx ON daily_summaries (workspace_id, summary_version, family, partition_date, dimension_key)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_directory (
            workspace_id uuid NOT NULL,
            person_id uuid NOT NULL,
            github_login text,
            display_name text,
            email text,
            team_id uuid,
            anonymize_after timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, person_id)
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS identity_directory_login_idx ON identity_directory (workspace_id, lower(github_login)) WHERE github_login IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_directory (
            workspace_id uuid NOT NULL,
            model_id uuid NOT NULL,
            provider_model_id text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, model_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS repository_directory (
            workspace_id uuid NOT NULL,
            repository_id uuid NOT NULL,
            full_name text NOT NULL,
            private boolean NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id, repository_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS named_export_audit (
            audit_id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            actor_person_id uuid NOT NULL,
            scope text NOT NULL,
            exported_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'analytics events are immutable';
        END;
        $$
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS events_immutable ON events
        """
    )

    op.execute(
        """
        CREATE TRIGGER events_immutable BEFORE UPDATE ON events
        FOR EACH ROW EXECUTE FUNCTION reject_event_mutation()
        """
    )


def downgrade() -> None:
    raise NotImplementedError
