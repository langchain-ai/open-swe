"""Job queue: background work and timers that used to run as Agent Server runs and crons.

A job is due at ``due_at``; a cron job moves to its next fire time after each run,
a one-shot job is deleted. ``key`` names a job that replaces any earlier one of the
same name. A worker holds a claimed job until ``locked_until``, so another replica
takes it again only if that worker died mid-run.
"""

from alembic import op

revision = "5b2d2caf7c5a"
down_revision = "d80046823007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE job (
            id uuid PRIMARY KEY,
            kind text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            due_at timestamptz NOT NULL,
            cron text,
            timezone text NOT NULL DEFAULT 'UTC',
            key text UNIQUE,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            attempts integer NOT NULL DEFAULT 0,
            locked_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX job_due_at ON job (due_at)")
    op.execute("CREATE INDEX job_metadata ON job USING gin (metadata jsonb_path_ops)")


def downgrade() -> None:
    raise NotImplementedError
