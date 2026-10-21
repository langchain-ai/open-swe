"""Add queue admission and candidates"""

from alembic import op

revision = "79a94ddd5d16"
down_revision = "4d846b42b226"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("""
        ALTER TABLE merge_queue_request
          ADD COLUMN dependencies jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(dependencies) = 'array'),
          ADD COLUMN admission jsonb CHECK (jsonb_typeof(admission) = 'object'),
          ADD COLUMN blocked_reason text,
          ADD COLUMN blocked_base_sha text CHECK (blocked_base_sha ~ '^[0-9a-f]{40}$'),
          ADD COLUMN queued_at timestamptz,
          ADD COLUMN last_checked_at timestamptz
    """)
    op.execute("""
        ALTER TABLE merge_queue_batch
          ADD COLUMN candidate jsonb CHECK (jsonb_typeof(candidate) = 'object'),
          ADD COLUMN build_attempts integer NOT NULL DEFAULT 0 CHECK (build_attempts >= 0),
          ADD COLUMN candidate_error text,
          ADD COLUMN published_at timestamptz,
          ADD COLUMN ref_deleted_at timestamptz,
          ADD CHECK (published_at IS NULL OR candidate IS NOT NULL),
          ADD CHECK (ref_deleted_at IS NULL OR (candidate IS NOT NULL AND finished_at IS NOT NULL))
    """)


def downgrade() -> None:
    raise NotImplementedError
