"""Record the review pages each person opens, for the Recent tab.

One row per person and pull request; ``updated_at`` moves on every visit.
"""

from alembic import op

revision = "65d8f8d3fe56"
down_revision = "4fd72cf43b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE review_page_visit (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            pull_request_id uuid NOT NULL REFERENCES pull_request(id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX review_page_visit_user_pull_request "
        "ON review_page_visit (user_id, pull_request_id)"
    )
    op.execute(
        "CREATE INDEX review_page_visit_user_recent ON review_page_visit (user_id, updated_at DESC)"
    )


def downgrade() -> None:
    raise NotImplementedError
