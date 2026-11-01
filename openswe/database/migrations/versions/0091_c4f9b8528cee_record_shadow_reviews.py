"""Record shadow reviews.

A sampled pull request gets a second reviewer on another model that follows its
reviews without publishing. Its findings and what each review would have posted
are kept here, to compare with the pull request's own findings offline.
"""

from alembic import op

revision = "c4f9b8528cee"
down_revision = ["1850da6129ee", "3c9e71d2a4b8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE review_shadow (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            pull_request_id uuid NOT NULL REFERENCES pull_request(id) ON DELETE CASCADE,
            reviewer_thread_id text NOT NULL,
            model_id text NOT NULL,
            reasoning_effort text NOT NULL,
            last_reviewed_sha text,
            findings jsonb NOT NULL DEFAULT '[]',
            publications jsonb NOT NULL DEFAULT '[]'
        )
    """)
    op.execute("CREATE UNIQUE INDEX review_shadow_pull_request ON review_shadow (pull_request_id)")
    op.execute(
        "CREATE UNIQUE INDEX review_shadow_reviewer_thread ON review_shadow (reviewer_thread_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
