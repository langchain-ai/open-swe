"""Review records and baby-sit watches moved out of the LangGraph Store.

``repository.review_enabled`` is the auto-review opt-in, ``NULL`` until someone
decides. ``review_style`` holds each repository's reviewer prompt and approval
mode, and ``review_assessment`` each published assessment by GitHub review id.
Feedback on assessments lives in ``user_record``. ``pull_request.task_id`` is the
task working on a pull request and ``task_state`` what it keeps there, such as a
baby-sit watch.
"""

from alembic import op

revision = "1563ad8bd725"
down_revision = ["7d34d8e5b6a3", "df7c28a904d8"]
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
            github_review_id bigint PRIMARY KEY,
            value jsonb NOT NULL,
            published_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        ALTER TABLE pull_request
            ADD COLUMN task_id uuid REFERENCES task (id) ON DELETE SET NULL,
            ADD COLUMN task_state jsonb
    """)
    op.execute(
        "CREATE INDEX pull_request_task_state_idx ON pull_request (repository_id) "
        "WHERE task_state IS NOT NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
