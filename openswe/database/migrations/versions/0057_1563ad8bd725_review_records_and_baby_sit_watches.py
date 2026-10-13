"""Review records and baby-sit watches moved out of the LangGraph Store.

``repository.review_enabled`` is the auto-review opt-in, ``NULL`` until someone
decides. ``review_style`` holds each repository's reviewer prompt and approval
mode, ``review_assessment`` each published assessment by GitHub review id, and
``baby_sit_watch`` each watched pull request. Feedback on assessments lives in
``user_record``.
"""

from alembic import op

revision = "1563ad8bd725"
down_revision = ["9ec28aa0b2a8", "b438344a06b7", "da651a8f67cc"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE repository ADD COLUMN review_enabled boolean")
    op.execute("""
        CREATE TABLE review_style (
            repository_id uuid PRIMARY KEY REFERENCES repository (id) ON DELETE CASCADE,
            custom_prompt text,
            approval_mode text CHECK (approval_mode IN ('off', 'dry_run', 'approve')),
            created_by text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE review_assessment (
            review_id bigint PRIMARY KEY,
            value jsonb NOT NULL,
            published_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE baby_sit_watch (
            key text PRIMARY KEY,
            owner text NOT NULL,
            repo text NOT NULL,
            active boolean NOT NULL,
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE INDEX baby_sit_watch_active_repo_idx ON baby_sit_watch (owner, repo) WHERE active"
    )


def downgrade() -> None:
    raise NotImplementedError
