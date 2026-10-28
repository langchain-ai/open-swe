"""Record each MDA report once by its key.

An MDA run sends its reports and notices in batches and sends again what it did
not hear was taken, to whichever replica answers. A row claims a key while a
delivery records it and keeps what it answered, so a later delivery of the key
records nothing twice.
"""

from alembic import op

revision = "4fd72cf43b71"
down_revision = "6416028c3ce3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE agent_report_receipt (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            thread_id text NOT NULL,
            idempotency_key text NOT NULL,
            reply jsonb
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX agent_report_receipt_key "
        "ON agent_report_receipt (thread_id, idempotency_key)"
    )
    op.execute("CREATE INDEX agent_report_receipt_created ON agent_report_receipt (created_at)")


def downgrade() -> None:
    raise NotImplementedError
