"""Keep the catalogs runs start with.

A run's catalog moves out of its context, which the Agent Server copies into the
run's config and its thread's and returns on every read of either. The backend
keeps it here by digest, and the run's token names the digest.
"""

from alembic import op

revision = "c28294118787"
down_revision = "6416028c3ce3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE run_catalog (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            digest text NOT NULL,
            catalog jsonb NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX run_catalog_digest ON run_catalog (digest)")
    op.execute("CREATE INDEX run_catalog_updated_at ON run_catalog (updated_at)")
    op.execute(
        "COMMENT ON TABLE run_catalog IS 'The catalog of each MDA run as the backend worked it "
        "out when it started the run, without its grants, kept by content; updated_at is the "
        "last run that started with it.'"
    )
    op.execute(
        "COMMENT ON COLUMN run_catalog.digest IS 'SHA-256 of the catalog as canonical JSON, "
        "which the run token signs.'"
    )


def downgrade() -> None:
    raise NotImplementedError
