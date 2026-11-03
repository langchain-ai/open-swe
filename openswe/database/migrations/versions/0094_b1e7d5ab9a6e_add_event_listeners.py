"""Add event listeners.

Python code that follows a pull request's events until it expires, one row per
thing followed, for every feature: a live Slack PR card is the first.
"""

from alembic import op

revision = "b1e7d5ab9a6e"
down_revision = ["c4f9b8528cee", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE event_listener (
            id uuid PRIMARY KEY,
            handler text NOT NULL,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            handler_state jsonb NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX event_listener_pull_request ON event_listener (pull_request_id, expires_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
