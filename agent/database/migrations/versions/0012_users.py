"""People, with one stable id across their GitHub and Slack identities.

The table is ``users`` because ``user`` is a reserved word in PostgreSQL.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id uuid PRIMARY KEY,
            email text NOT NULL DEFAULT '',
            display_name text NOT NULL DEFAULT '',
            avatar_url text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX users_email_idx ON users (lower(email)) WHERE email <> ''
        """
    )

    op.execute(
        """
        CREATE TABLE user_identity (
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            provider text NOT NULL CHECK (provider IN ('github', 'slack')),
            external_id text NOT NULL,
            login text NOT NULL DEFAULT '',
            team_id text NOT NULL DEFAULT '',
            linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (provider, external_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX user_identity_user_idx ON user_identity (user_id)
        """
    )

    op.execute(
        """
        CREATE INDEX user_identity_login_idx
            ON user_identity (provider, lower(login)) WHERE login <> ''
        """
    )


def downgrade() -> None:
    raise NotImplementedError
