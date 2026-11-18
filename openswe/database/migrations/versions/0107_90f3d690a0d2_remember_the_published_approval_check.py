"""Remember the published approval check.

Robin re-evaluates its approval check on every pull request and review event, and
posted the check and listed the conversation to find its note each time. Keeping
what it last published, and the note's comment id, lets it skip both when nothing changed.
"""

from alembic import op

revision = "90f3d690a0d2"
down_revision = ["32c5ee89381e", "606fff7c4ea3", "79de2247aef7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE pull_request ADD COLUMN approval_check_head_sha text NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE pull_request ADD COLUMN approval_check_verdict text NOT NULL DEFAULT ''"
    )
    op.execute("ALTER TABLE pull_request ADD COLUMN approval_note_id bigint")


def downgrade() -> None:
    raise NotImplementedError
