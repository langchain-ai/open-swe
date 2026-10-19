"""Job and thread index in the shared table shape.

``job`` gains ``updated_at`` and who wrote it: a person who saves an automation
owns its jobs; every other job is the system's, with NULL attribution.
``thread_index`` gets its own ``id`` key, keeping the Agent Server's
``thread_id`` as a unique natural key. Its writes come through the Agent
Server API, which names no person, so ``created_by`` and ``updated_by`` stay
NULL until it does.
"""

from alembic import op

revision = "c8d8a5c65df3"
down_revision = "6fb9377b47b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE job
            ADD COLUMN updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            ADD COLUMN updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        """
    )
    op.execute("UPDATE job SET updated_at = created_at")
    op.execute(
        """
        ALTER TABLE thread_index
            ADD COLUMN id uuid,
            ADD COLUMN created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            ADD COLUMN updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        """
    )
    op.execute("UPDATE thread_index SET id = gen_random_uuid()")
    op.execute("ALTER TABLE thread_index ALTER COLUMN id SET NOT NULL")
    op.execute("ALTER TABLE thread_index DROP CONSTRAINT thread_index_pkey")
    op.execute("ALTER TABLE thread_index ADD PRIMARY KEY (id)")
    op.execute("CREATE UNIQUE INDEX thread_index_thread_id ON thread_index (thread_id)")


def downgrade() -> None:
    raise NotImplementedError
