"""Admin announcements: one message DMed to everyone Open SWE can reach on Slack.

Recipients are fixed when the announcement is created, one
``announcement_delivery`` row each, so a send that is retried or resumed after a
restart walks the same list and never DMs a person twice: a row leaves
``queued`` before its DM is posted, and a row found mid-send is never retried.
"""

from alembic import op

revision = "2cd871a2607d"
down_revision = ["7d34d8e5b6a3", "df7c28a904d8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE announcement (
            id uuid PRIMARY KEY,
            text text NOT NULL,
            author_id uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            finished_at timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE announcement_delivery (
            announcement_id uuid NOT NULL REFERENCES announcement (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'sending', 'sent', 'failed')),
            error text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (announcement_id, user_id)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
