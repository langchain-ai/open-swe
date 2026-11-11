"""Record Ask PR requesters and cache the Robin Approves requirement"""

from uuid import uuid7

import sqlalchemy as sa
from alembic import op

revision = "c04ec5f250f1"
down_revision = "0aae6a97dd3b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pull_request_requester (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            login text NOT NULL,
            user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX pull_request_requester_key "
        "ON pull_request_requester (pull_request_id, lower(login))"
    )
    # Ask PRs opened before requesters were kept name theirs in the description.
    rows = op.get_bind().execute(
        sa.text(
            r"""
            SELECT pr.id, found.login, identity.user_id
            FROM pull_request pr
            CROSS JOIN LATERAL (
                SELECT substring(pr.body from 'Requested by @([A-Za-z0-9-]+) via an Ask channel')
                    AS login
            ) found
            LEFT JOIN user_identity identity
              ON identity.provider = 'github' AND lower(identity.login) = lower(found.login)
            WHERE found.login IS NOT NULL
            """
        )
    )
    for pull_request_id, login, user_id in rows.all():
        op.execute(
            sa.text(
                "INSERT INTO pull_request_requester (id, pull_request_id, login, user_id) "
                "VALUES (:id, :pull_request_id, :login, :user_id) ON CONFLICT DO NOTHING"
            ).bindparams(id=uuid7(), pull_request_id=pull_request_id, login=login, user_id=user_id)
        )
    op.execute(
        "ALTER TABLE pull_request ADD COLUMN auto_approved_head_sha text NOT NULL DEFAULT ''"
    )
    # Open pull requests Robin opened as itself from an Ask channel before requesters were
    # kept, whose requester the backfill couldn't find: nobody knows whose approval to refuse.
    op.execute(
        "ALTER TABLE pull_request ADD COLUMN requester_unknown boolean NOT NULL DEFAULT false"
    )
    op.execute(
        """
        UPDATE pull_request pr SET requester_unknown = true
        WHERE pr.state IN ('open', 'draft')
          AND pr.author LIKE '%[bot]'
          AND EXISTS (
              SELECT 1 FROM workspace_slack_channel channel
              WHERE channel.ask AND channel.channel_id = upper(pr.slack_channel_id)
          )
          AND NOT EXISTS (
              SELECT 1 FROM pull_request_requester requester
              WHERE requester.pull_request_id = pr.id
          )
        """
    )
    op.execute("ALTER TABLE repository ADD COLUMN approval_check_required boolean")
    op.execute("ALTER TABLE repository ADD COLUMN approval_check_checked_at timestamptz")


def downgrade() -> None:
    raise NotImplementedError
