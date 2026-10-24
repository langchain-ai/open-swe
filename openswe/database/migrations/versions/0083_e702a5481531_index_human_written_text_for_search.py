"""Index human-written text for search"""

from alembic import op

revision = "e702a5481531"
down_revision = ["14cf183ef936", "9553a7e0a493"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    # A row is a Slack message or a message in an Open SWE thread, never both.
    # ``created_by`` is its author, when they have an Open SWE account.
    op.execute(
        """
        CREATE TABLE human_text (
            id uuid PRIMARY KEY,
            slack_channel_id text REFERENCES slack_channel (id) ON DELETE CASCADE,
            slack_ts text,
            slack_thread_ts text,
            slack_user_id text,
            thread_id text REFERENCES thread (thread_id) ON DELETE CASCADE,
            message_id text,
            body text NOT NULL,
            sent_at timestamptz NOT NULL,
            embedding vector(1536),
            body_search tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            CHECK (
                (
                    slack_channel_id IS NOT NULL AND slack_ts IS NOT NULL
                    AND slack_user_id IS NOT NULL
                    AND thread_id IS NULL AND message_id IS NULL
                )
                OR (
                    thread_id IS NOT NULL AND message_id IS NOT NULL
                    AND slack_channel_id IS NULL AND slack_ts IS NULL
                    AND slack_thread_ts IS NULL AND slack_user_id IS NULL
                )
            )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX human_text_slack_message_idx ON human_text (slack_channel_id, slack_ts) "
        "WHERE slack_channel_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX human_text_thread_message_idx ON human_text (thread_id, message_id) "
        "WHERE thread_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX human_text_embedding_idx ON human_text "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX human_text_body_search_idx ON human_text USING gin (body_search)")
    op.execute("CREATE INDEX human_text_unembedded_idx ON human_text (id) WHERE embedding IS NULL")
    op.execute("CREATE INDEX human_text_created_by_idx ON human_text (created_by)")


def downgrade() -> None:
    raise NotImplementedError
