"""Cache required checks by repository and branch"""

from alembic import op

revision = "51127aaa9d2e"
down_revision = ["2e0eb2690dd8", "4a8f25fefeb8", "981024b5de47"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE required_check_cache (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
            branch text NOT NULL,
            checks jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX required_check_cache_repository_branch
            ON required_check_cache(repository_id, branch)
    """)


def downgrade() -> None:
    raise NotImplementedError
