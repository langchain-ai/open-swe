"""Shared walkthrough plan"""

from alembic import op

revision = "9ec28aa0b2a8"
down_revision = ["4ce55eec2786", "42e9e3af43e3", "b58096f6b755", "f11526182620"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE pull_request_walkthrough_file")
    op.execute("DROP TABLE pull_request_walkthrough_step")
    # Stored steps hold line numbers without the lines' text, so they cannot be followed
    # by content; each PR keeps its human input and is planned again from scratch.
    op.execute(
        """
        ALTER TABLE pull_request_walkthrough
            DROP COLUMN scout_thread_id,
            ADD COLUMN plan jsonb,
            ADD COLUMN complete boolean NOT NULL DEFAULT false
        """
    )
    # No head, so the first planner at the PR's head rebuilds it from the diff, textless files included.
    op.execute(
        "UPDATE pull_request_walkthrough SET head_sha = '', plan = jsonb_build_object('head_sha', '')"
    )
    op.execute("ALTER TABLE pull_request_walkthrough ALTER COLUMN plan SET NOT NULL")
    # Readers keep every line they approved; only their place in the old walk is dropped.
    op.execute("UPDATE review_guide_session SET walkthrough = NULL")


def downgrade() -> None:
    raise NotImplementedError
