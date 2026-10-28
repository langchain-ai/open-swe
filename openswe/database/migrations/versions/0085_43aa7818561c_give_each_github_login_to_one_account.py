"""Give each GitHub login to one account.

GitHub hands a login to someone else once its owner renames, and both accounts
keep it here until the first one signs in again. The account seen most recently
is the one GitHub knows by it now; the others forget it, and a unique index
stops two accounts from holding it again.
"""

from alembic import op

revision = "43aa7818561c"
down_revision = "4fd72cf43b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        UPDATE user_identity AS stale
        SET login = ''
        FROM (
            SELECT external_id, row_number() OVER (
                PARTITION BY lower(login) ORDER BY last_seen_at DESC, external_id
            ) AS recency
            FROM user_identity
            WHERE provider = 'github' AND login <> ''
        ) AS ranked
        WHERE stale.provider = 'github'
            AND stale.external_id = ranked.external_id
            AND ranked.recency > 1
    """)
    op.execute("""
        CREATE UNIQUE INDEX user_identity_github_login
            ON user_identity (lower(login)) WHERE provider = 'github' AND login <> ''
    """)


def downgrade() -> None:
    raise NotImplementedError
