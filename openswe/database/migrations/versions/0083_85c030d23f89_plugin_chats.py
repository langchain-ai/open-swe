"""Plugin chats, the agent a plugin works with beside it.

``plugin_chat`` is one person's chat with an agent in the dock beside a plugin:
the agent thread, under a name the plugin chose and optionally a repository,
and the tools the plugin's frame last offered it with instructions for using
them. Every run of the thread gets those tools.

``plugin_chat_call`` is one call the agent made to such a tool. The run ends
there; the dock beside the plugin runs the call in the plugin's frame and
resumes the thread with its result, marking the call answered.

Plugins no longer start threads of their own, so ``plugin_thread`` goes, and
installs that were granted ``threads`` hold ``agent`` instead.
"""

from alembic import op

revision = "85c030d23f89"
down_revision = "90b7fdb855a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE plugin_chat (
            id uuid PRIMARY KEY,
            installation uuid NOT NULL REFERENCES plugin_installation (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            repository text,
            thread_id text NOT NULL,
            tool_specs jsonb NOT NULL DEFAULT '[]',
            instructions text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX plugin_chat_key
            ON plugin_chat (installation, user_id, name, repository) NULLS NOT DISTINCT
    """)
    op.execute("CREATE UNIQUE INDEX plugin_chat_thread_id ON plugin_chat (thread_id)")
    op.execute("""
        CREATE TABLE plugin_chat_call (
            id uuid PRIMARY KEY,
            chat uuid NOT NULL REFERENCES plugin_chat (id) ON DELETE CASCADE,
            call_id text NOT NULL,
            tool text NOT NULL,
            arguments jsonb NOT NULL,
            answered_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX plugin_chat_call_key ON plugin_chat_call (chat, call_id)")
    op.execute("DROP TABLE plugin_thread")
    op.execute("""
        UPDATE plugin_installation
        SET manifest = jsonb_set(
            manifest,
            '{permissions}',
            (
                SELECT jsonb_agg(CASE WHEN p = '"threads"' THEN '"agent"'::jsonb ELSE p END)
                FROM jsonb_array_elements(manifest -> 'permissions') AS p
            )
        )
        WHERE manifest -> 'permissions' ? 'threads'
    """)


def downgrade() -> None:
    raise NotImplementedError
