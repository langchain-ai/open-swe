from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = """
        ALTER TABLE run_cost_refresh
            ADD COLUMN state text NOT NULL DEFAULT 'legacy_unmapped'
                CHECK (state IN ('legacy_unmapped', 'pending', 'leased', 'awaiting_delivery',
                                 'complete', 'needs_attention')),
            ADD COLUMN invocation_id text,
            ADD COLUMN thread_id text,
            ADD COLUMN invocation_started_at text,
            ADD COLUMN project_name text,
            ADD COLUMN attempts integer NOT NULL DEFAULT 0,
            ADD COLUMN retry_started_at timestamptz,
            ADD COLUMN next_attempt_at timestamptz,
            ADD COLUMN last_attempt_at timestamptz,
            ADD COLUMN lease_until timestamptz,
            ADD COLUMN claim_token uuid,
            ADD COLUMN error_code text,
            ADD COLUMN cost_event_id uuid,
            ADD COLUMN completed_at timestamptz;
        CREATE INDEX run_cost_refresh_due_idx ON run_cost_refresh (next_attempt_at)
            WHERE state IN ('pending', 'leased');
        CREATE TABLE cost_recovery_dispatcher (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            claim_token uuid,
            available_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        INSERT INTO cost_recovery_dispatcher (singleton) VALUES (true);
        REVOKE ALL ON run_cost_refresh, cost_recovery_dispatcher FROM PUBLIC;
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
