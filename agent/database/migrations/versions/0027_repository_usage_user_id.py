"""Associate repository history with stable user identities."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_repository_usage ADD COLUMN user_id uuid")
    op.execute("""
        UPDATE user_repository_usage AS usage
        SET user_id = (
            SELECT identity.user_id FROM user_identity AS identity
            WHERE identity.provider = 'github'
                AND lower(identity.login) = lower(usage.login)
            ORDER BY identity.last_seen_at DESC
            LIMIT 1
        )
    """)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM user_repository_usage WHERE user_id IS NULL) THEN
                RAISE EXCEPTION 'Repository usage has unresolved GitHub logins; reconcile user identities before migrating';
            END IF;
        END $$
    """)
    op.execute("""
        CREATE TEMP TABLE repository_usage_merged ON COMMIT DROP AS
        SELECT user_id, repo, sum(use_count)::bigint AS use_count,
            max(last_used_at) AS last_used_at
        FROM user_repository_usage GROUP BY user_id, repo
    """)
    op.execute("DELETE FROM user_repository_usage")
    op.execute("ALTER TABLE user_repository_usage DROP CONSTRAINT user_repository_usage_pkey")
    op.execute("ALTER TABLE user_repository_usage DROP COLUMN login")
    op.execute("ALTER TABLE user_repository_usage ALTER COLUMN user_id SET NOT NULL")
    op.execute("ALTER TABLE user_repository_usage ADD PRIMARY KEY (user_id, repo)")
    op.execute("""
        ALTER TABLE user_repository_usage ADD CONSTRAINT user_repository_usage_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    """)
    op.execute("""
        INSERT INTO user_repository_usage (user_id, repo, use_count, last_used_at)
        SELECT user_id, repo, use_count, last_used_at FROM repository_usage_merged
    """)


def downgrade() -> None:
    raise NotImplementedError
