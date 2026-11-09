"""Record finding dismissals"""

from alembic import op

revision = "d0b16544a302"
down_revision = ["c4f9b8528cee", "51127aaa9d2e", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pull_request_finding_dismissal (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL,
            finding_id text NOT NULL,
            reason text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (pull_request_id, finding_id)
                REFERENCES pull_request_finding (pull_request_id, id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX pull_request_finding_dismissal_finding_idx
            ON pull_request_finding_dismissal (pull_request_id, finding_id)
    """)
    op.execute("""
        CREATE INDEX pull_request_finding_dismissal_created_by_idx
            ON pull_request_finding_dismissal (created_by)
    """)
    op.execute("""
        CREATE INDEX pull_request_finding_dismissal_updated_by_idx
            ON pull_request_finding_dismissal (updated_by)
    """)
    op.execute("""
        COMMENT ON TABLE pull_request_finding_dismissal IS
            'A person dismissing one of the reviewer''s findings from the dashboard, and why.'
    """)


def downgrade() -> None:
    raise NotImplementedError
