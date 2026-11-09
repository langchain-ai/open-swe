"""Add web apps: versions, installs, storage, rooms and chats.

An app (``plugin``) belongs to whoever published it (``owner``), under a ``name``.
A ``listed`` app is one anyone may add to their own web UI; an unlisted one only its
owner. ``plugin_bundle`` keeps each distinct HTML bundle once, by its SHA-256.

``plugin_version`` is one immutable build of an app: its manifest, read from inside
the bundle, and the bundle. Where it was built is its ``origin``, kept only to show.
One with ``preview_of`` is the preview a thread's agent is building there, not a
release.

``plugin_installation`` is one person adding an app to their web UI, pinned to a
version; nobody installs for anyone else. One with ``preview_of`` runs that thread's
preview beside it, outside the sidebar. ``plugin_repository_grant`` is one repository
a person shared with an installation by picking it when the app asked.

``plugin_document`` is an app's storage: each document is either shared with everyone
who can read ``repository`` or private to ``user_id``. ``plugin_room_update`` is a live
room's log: the opaque updates its members broadcast, in ``seq`` order, scoped like
documents; a member may replace the log up to some ``seq`` with one snapshot.

``plugin_chat`` is one person's chat with the agent in the dock beside an app, with
the tools the app's frame last offered it and their instructions. ``plugin_chat_call``
is one call the agent made to such a tool: a dock claims it, runs it in the app's
frame and answers it, and a run delivers the answer to the agent.
"""

from alembic import op

revision = "655a0b3fb348"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None

_AUDIT = """
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL"""


def upgrade() -> None:
    # Every statement can run again: a preview database keeps these tables after it
    # forgets the stamp of a branch that left the preview tree.
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin (
            id uuid PRIMARY KEY,
            owner uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            listed boolean NOT NULL DEFAULT false,{_AUDIT}
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS plugin_name ON plugin (owner, name)")
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_bundle (
            id uuid PRIMARY KEY,
            sha256 text NOT NULL,
            html text NOT NULL,{_AUDIT}
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS plugin_bundle_sha256 ON plugin_bundle (sha256)")
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_version (
            id uuid PRIMARY KEY,
            plugin uuid NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
            number integer NOT NULL,
            manifest jsonb NOT NULL,
            bundle uuid NOT NULL REFERENCES plugin_bundle (id),
            origin jsonb NOT NULL DEFAULT '{{}}',
            preview_of text,{_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS plugin_version_number ON plugin_version (plugin, number)"
    )
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_installation (
            id uuid PRIMARY KEY,
            plugin uuid NOT NULL REFERENCES plugin (id),
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            version uuid NOT NULL REFERENCES plugin_version (id),
            preview_of text,{_AUDIT}
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_installation_app
            ON plugin_installation (user_id, plugin) WHERE preview_of IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_installation_preview
            ON plugin_installation (preview_of) WHERE preview_of IS NOT NULL
    """)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_repository_grant (
            id uuid PRIMARY KEY,
            installation uuid NOT NULL REFERENCES plugin_installation (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            repository text NOT NULL,{_AUDIT}
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_repository_grant_key
            ON plugin_repository_grant (installation, user_id, repository)
    """)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_document (
            id uuid PRIMARY KEY,
            plugin uuid NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
            collection text NOT NULL,
            key text NOT NULL,
            repository text,
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            document jsonb NOT NULL,{_AUDIT},
            CONSTRAINT plugin_document_one_scope CHECK ((repository IS NULL) <> (user_id IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_document_key
            ON plugin_document (plugin, collection, repository, user_id, key) NULLS NOT DISTINCT
    """)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_room_update (
            id uuid PRIMARY KEY,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            plugin uuid NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
            room text NOT NULL,
            repository text,
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            payload bytea NOT NULL,{_AUDIT},
            CONSTRAINT plugin_room_update_one_scope CHECK ((repository IS NULL) <> (user_id IS NULL))
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS plugin_room_update_seq ON plugin_room_update (seq)"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS plugin_room_update_room
            ON plugin_room_update (plugin, room, repository, user_id, seq)
    """)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_chat (
            id uuid PRIMARY KEY,
            installation uuid NOT NULL REFERENCES plugin_installation (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            repository text,
            thread_id text NOT NULL,
            tool_specs jsonb NOT NULL DEFAULT '[]',
            instructions text NOT NULL DEFAULT '',
            automatic_resumes integer NOT NULL DEFAULT 0,{_AUDIT}
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_chat_key
            ON plugin_chat (installation, user_id, name, repository) NULLS NOT DISTINCT
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS plugin_chat_thread_id ON plugin_chat (thread_id)")
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS plugin_chat_call (
            id uuid PRIMARY KEY,
            chat uuid NOT NULL REFERENCES plugin_chat (id) ON DELETE CASCADE,
            call_id text NOT NULL,
            tool text NOT NULL,
            arguments jsonb NOT NULL,
            claimed_at timestamptz,
            answered_at timestamptz,
            output text,
            delivered_at timestamptz,{_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS plugin_chat_call_key ON plugin_chat_call (chat, call_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
