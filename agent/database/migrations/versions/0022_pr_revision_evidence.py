"""Retain authoritative PR revision endpoints independently of measurements."""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pr_revision_evidence (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            repository_full_name text NOT NULL,
            pr_number integer NOT NULL CHECK (pr_number > 0),
            endpoint_kind text NOT NULL CHECK (endpoint_kind IN ('opening', 'final')),
            base_sha text NOT NULL CHECK (base_sha ~ '^[0-9a-f]{40}$'),
            head_sha text NOT NULL CHECK (head_sha ~ '^[0-9a-f]{40}$'),
            endpoint_at timestamptz NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            source_kind text NOT NULL CHECK (source_kind IN ('creation_response', 'webhook')),
            source_id text,
            UNIQUE (workspace_id, repository_full_name, pr_number, endpoint_kind)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
