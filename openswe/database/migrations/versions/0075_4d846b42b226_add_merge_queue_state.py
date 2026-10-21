"""Add merge queue state. No queue workers or repositories are enabled."""

from alembic import op

revision = "4d846b42b226"
down_revision = "87e4182be853"
branch_labels = None
depends_on = None


# All tables are new. Foreign keys take only brief locks on existing identity tables.
_TABLES = {
    "merge_queue": """
        workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
        repository_id uuid NOT NULL REFERENCES repository(id),
        target_branch text NOT NULL CHECK (target_branch <> '' AND target_branch NOT LIKE 'refs/%'),
        paused boolean NOT NULL DEFAULT true,
        fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
        lease_owner uuid,
        lease_until timestamptz,
        CHECK ((lease_owner IS NULL) = (lease_until IS NULL))
    """,
    "merge_queue_request": """
        queue_id uuid NOT NULL REFERENCES merge_queue(id) ON DELETE CASCADE,
        pull_request_id uuid NOT NULL REFERENCES pull_request(id),
        request_key text NOT NULL CHECK (request_key <> ''),
        head_sha text NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$'),
        state text NOT NULL DEFAULT 'waiting_for_review'
            CHECK (state IN ('waiting_for_review', 'queued', 'batched', 'merged', 'withdrawn')),
        priority integer NOT NULL DEFAULT 0,
        finished_at timestamptz,
        CHECK ((state IN ('merged', 'withdrawn')) = (finished_at IS NOT NULL))
    """,
    "merge_queue_batch": """
        queue_id uuid NOT NULL REFERENCES merge_queue(id) ON DELETE CASCADE,
        request_key text NOT NULL CHECK (request_key <> ''),
        base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
        policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
        deadline timestamptz NOT NULL,
        max_attempts integer NOT NULL CHECK (max_attempts > 0),
        max_plans integer NOT NULL CHECK (max_plans >= 0),
        state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'landed', 'invalidated')),
        finished_at timestamptz,
        CHECK ((state <> 'active') = (finished_at IS NOT NULL))
    """,
    "merge_queue_member": """
        batch_id uuid NOT NULL REFERENCES merge_queue_batch(id) ON DELETE CASCADE,
        request_id uuid NOT NULL REFERENCES merge_queue_request(id) ON DELETE CASCADE,
        position integer NOT NULL CHECK (position >= 0),
        head_sha text NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$')
    """,
    "merge_queue_attempt": """
        batch_id uuid NOT NULL REFERENCES merge_queue_batch(id) ON DELETE CASCADE,
        request_key text NOT NULL CHECK (request_key <> ''),
        number integer NOT NULL CHECK (number > 0),
        deadline timestamptz NOT NULL,
        state text NOT NULL DEFAULT 'running'
            CHECK (state IN ('running', 'succeeded', 'failed', 'timed_out', 'cancelled')),
        finished_at timestamptz,
        CHECK ((state <> 'running') = (finished_at IS NOT NULL))
    """,
    "merge_queue_evidence": """
        attempt_id uuid NOT NULL REFERENCES merge_queue_attempt(id) ON DELETE CASCADE,
        key text NOT NULL CHECK (key <> ''),
        reference jsonb NOT NULL CHECK (jsonb_typeof(reference) = 'object')
    """,
    "merge_queue_decision": """
        batch_id uuid NOT NULL REFERENCES merge_queue_batch(id) ON DELETE CASCADE,
        request_key text NOT NULL CHECK (request_key <> ''),
        plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
        lease_owner uuid NOT NULL,
        fence bigint NOT NULL CHECK (fence > 0)
    """,
}


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, columns in _TABLES.items():
        op.execute(f"""
            CREATE TABLE {table} (
                id uuid PRIMARY KEY,
                created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                created_by uuid REFERENCES users(id) ON DELETE SET NULL,
                updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
                {columns}
            )
        """)
    for statement in (
        "CREATE UNIQUE INDEX merge_queue_owner ON merge_queue (repository_id, target_branch)",
        "CREATE UNIQUE INDEX merge_queue_request_key ON merge_queue_request (queue_id, request_key)",
        "CREATE UNIQUE INDEX merge_queue_request_active ON merge_queue_request (queue_id, pull_request_id) WHERE finished_at IS NULL",
        "CREATE INDEX merge_queue_request_order ON merge_queue_request (queue_id, priority DESC, created_at, id) WHERE finished_at IS NULL",
        "CREATE INDEX merge_queue_request_retention ON merge_queue_request (queue_id, finished_at) WHERE finished_at IS NOT NULL",
        "CREATE UNIQUE INDEX merge_queue_batch_key ON merge_queue_batch (queue_id, request_key)",
        "CREATE UNIQUE INDEX merge_queue_batch_active ON merge_queue_batch (queue_id) WHERE state = 'active'",
        "CREATE INDEX merge_queue_batch_retention ON merge_queue_batch (queue_id, finished_at) WHERE finished_at IS NOT NULL",
        "CREATE UNIQUE INDEX merge_queue_member_position ON merge_queue_member (batch_id, position)",
        "CREATE UNIQUE INDEX merge_queue_member_request ON merge_queue_member (batch_id, request_id)",
        "CREATE INDEX merge_queue_member_history ON merge_queue_member (request_id)",
        "CREATE UNIQUE INDEX merge_queue_attempt_key ON merge_queue_attempt (batch_id, request_key)",
        "CREATE UNIQUE INDEX merge_queue_attempt_number ON merge_queue_attempt (batch_id, number)",
        "CREATE UNIQUE INDEX merge_queue_attempt_active ON merge_queue_attempt (batch_id) WHERE state = 'running'",
        "CREATE UNIQUE INDEX merge_queue_evidence_key ON merge_queue_evidence (attempt_id, key)",
        "CREATE UNIQUE INDEX merge_queue_decision_key ON merge_queue_decision (batch_id, request_key)",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
