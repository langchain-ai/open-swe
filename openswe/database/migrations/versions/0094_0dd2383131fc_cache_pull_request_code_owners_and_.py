"""Cache pull request code owners and Slack channel members"""

from alembic import op

revision = "0dd2383131fc"
down_revision = ["c4f9b8528cee", "51127aaa9d2e", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE code_owner_cache (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
            number integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX code_owner_cache_repository_number
            ON code_owner_cache(repository_id, number)
    """)
    op.execute("""
        CREATE TABLE code_owner_cache_user (
            id uuid PRIMARY KEY,
            cache_id uuid NOT NULL REFERENCES code_owner_cache(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            alternatives integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX code_owner_cache_user_cache_user
            ON code_owner_cache_user(cache_id, user_id)
    """)
    op.execute("CREATE INDEX code_owner_cache_user_user ON code_owner_cache_user(user_id)")
    op.execute("ALTER TABLE slack_channel ADD COLUMN members_synced_at timestamptz")
    op.execute("""
        CREATE TABLE slack_channel_member (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_channel_member_channel_user
            ON slack_channel_member(channel_id, user_id)
    """)
    op.execute("CREATE INDEX slack_channel_member_user ON slack_channel_member(user_id)")


def downgrade() -> None:
    raise NotImplementedError
