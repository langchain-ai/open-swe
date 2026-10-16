"""Automations, triggers, and event claims"""

from alembic import op

revision = "f4030c2b06bc"
down_revision = "37e4a81fa83f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE automation (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
            name text NOT NULL,
            prompt text NOT NULL,
            slack_channel_id text,
            slack_notification_mode text NOT NULL DEFAULT 'always'
                CHECK (slack_notification_mode IN ('always', 'on_action')),
            admin_thread boolean NOT NULL DEFAULT false,
            model text NOT NULL DEFAULT 'Default',
            effort text,
            base_branch text NOT NULL DEFAULT 'main',
            branch_prefix text,
            enabled boolean NOT NULL DEFAULT true,
            created_by text NOT NULL DEFAULT '',
            updated_by text NOT NULL DEFAULT '',
            user_email text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            last_thread_id text,
            last_run_id text,
            last_triggered_at timestamptz,
            last_error text,
            last_error_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX automation_workspace_idx ON automation (workspace_id)")
    op.execute(
        """
        CREATE TABLE automation_trigger (
            id uuid PRIMARY KEY,
            automation_id uuid NOT NULL REFERENCES automation (id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('schedule', 'github')),
            config jsonb NOT NULL,
            match_key text,
            cron_id text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX automation_trigger_automation_idx ON automation_trigger (automation_id)"
    )
    op.execute(
        "CREATE INDEX automation_trigger_match_idx ON automation_trigger (kind, match_key) "
        "WHERE match_key IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE event_claim (
            scope text NOT NULL,
            key text NOT NULL,
            claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (scope, key)
        )
        """
    )
    op.execute("CREATE INDEX event_claim_expires_idx ON event_claim (expires_at)")
    for statement in (
        "COMMENT ON TABLE automation IS 'A stored prompt that runs in one workspace whenever "
        "one of its triggers fires. Run state lives on the row.'",
        "COMMENT ON TABLE automation_trigger IS 'What fires an automation: a cron schedule or "
        "matching GitHub events. config holds each kind''s own filters, such as a GitHub "
        "trigger''s repository and events, and is validated by the application; match_key is "
        "the lowercased repository for GitHub triggers.'",
        "COMMENT ON TABLE event_claim IS 'Exactly-once claims for inbound events, keyed by a "
        "scope and a key such as automation id plus delivery id. A claim can be released so a "
        "retry runs, and is reclaimable once expired.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
