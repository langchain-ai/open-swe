"""Keep each incident run's evidence.

An incident run's tools record what they found as evidence its report may cite. The
run's calls reach any backend replica, so the evidence is kept here, one row per run.
"""

from alembic import op

revision = "4cbde59060e3"
down_revision = ["1850da6129ee", "3c9e71d2a4b8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE incident_run_evidence (
            id uuid PRIMARY KEY,
            incident uuid NOT NULL REFERENCES incident (id) ON DELETE CASCADE,
            run_id text NOT NULL,
            collected jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX incident_run_evidence_run ON incident_run_evidence (incident, run_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
