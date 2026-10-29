"""Keep the tools MDA runs start with.

A run's context names its tools and carries none of their specs; the run reads
them from the backend. The backend keeps every tool a run started with here, once
per distinct set, and the run's token names the set's digest.
"""

from alembic import op

revision = "00ba3da33037"
down_revision = "4fd72cf43b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE run_tool_sets (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            digest text NOT NULL,
            tools jsonb NOT NULL,
            excluded jsonb NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX run_tool_sets_digest ON run_tool_sets (digest)")
    op.execute("CREATE INDEX run_tool_sets_updated_at ON run_tool_sets (updated_at)")
    op.execute(
        "COMMENT ON TABLE run_tool_sets IS 'Every tool an MDA run started with, without "
        "its grants, kept once per distinct set; updated_at is the last run that started "
        "with it.'"
    )
    op.execute(
        "COMMENT ON COLUMN run_tool_sets.excluded IS 'Names of the tools the graph keeps "
        "from the model: read by a run that lists them, never found by search.'"
    )
    op.execute(
        "COMMENT ON COLUMN run_tool_sets.digest IS 'SHA-256 of the tools as canonical "
        "JSON, which the run token signs.'"
    )


def downgrade() -> None:
    raise NotImplementedError
