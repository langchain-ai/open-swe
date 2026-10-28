"""Keep the operations of the runs MDA plays.

A run's context names its operations and carries none of their specs; the run
reads them from the backend. The backend keeps every operation a run started with
here, once per distinct set, and the run's token names its digest.
"""

from alembic import op

revision = "00ba3da33037"
down_revision = "4fd72cf43b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE run_operations (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            digest text NOT NULL,
            operations jsonb NOT NULL,
            excluded jsonb NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX run_operations_digest ON run_operations (digest)")
    op.execute("CREATE INDEX run_operations_updated_at ON run_operations (updated_at)")
    op.execute(
        "COMMENT ON TABLE run_operations IS 'Every operation an MDA run started with, "
        "without its grants, kept once per distinct set; updated_at is the last run that "
        "started with it.'"
    )
    op.execute(
        "COMMENT ON COLUMN run_operations.excluded IS 'Names of the operations the graph "
        "keeps from the model: read by a run that lists them, never found by search.'"
    )
    op.execute(
        "COMMENT ON COLUMN run_operations.digest IS 'SHA-256 of the operations as canonical "
        "JSON, which the run token signs.'"
    )


def downgrade() -> None:
    raise NotImplementedError
