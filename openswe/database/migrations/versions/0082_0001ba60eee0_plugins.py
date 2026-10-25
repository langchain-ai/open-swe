"""Plugins installed from GitHub repositories.

``plugin`` is one plugin source, a directory of a GitHub repository; every
installation of it, and every document it stores, hangs off this row, so a
plugin of the same name from another repository never shares its data.
``plugin_bundle`` keeps each distinct UI bundle once, by its SHA-256.
``plugin_installation`` is one install at a pinned commit: for one person
(``user_id``), or for everyone when ``user_id`` is NULL. Its ``manifest`` is the
plugin's manifest at that commit, and the permissions it lists are the ones the
installer granted; ``repositories`` are the GitHub repositories the plugin may
reach. ``plugin_document`` is the plugin's storage: each document is either
shared with everyone who can read ``repository`` or private to ``user_id``.
``plugin_thread`` records the agent threads a plugin started.
``plugin_room_update`` is a live room's log: the opaque updates its members
broadcast (a CRDT's, for collaborative editing), in ``seq`` order, scoped like
documents. A member may replace the log up to some ``seq`` with one snapshot.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001ba60eee0"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        )
    )


def upgrade() -> None:
    # Every statement can run again: a preview database may keep these tables after
    # forgetting it stamped them, when its branch left the preview tree for a while.
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin (
            id uuid PRIMARY KEY,
            repository text NOT NULL,
            path text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    # A later migration drops the columns these indexes cover.
    if _has_column("plugin", "repository"):
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS plugin_source ON plugin (repository, path)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_bundle (
            id uuid PRIMARY KEY,
            sha256 text NOT NULL,
            html text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS plugin_bundle_sha256 ON plugin_bundle (sha256)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_installation (
            id uuid PRIMARY KEY,
            plugin uuid NOT NULL REFERENCES plugin (id),
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            ref text NOT NULL,
            commit_sha text NOT NULL,
            manifest jsonb NOT NULL,
            bundle uuid NOT NULL REFERENCES plugin_bundle (id),
            repositories text[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    if _has_column("plugin_installation", "manifest"):
        op.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS plugin_installation_name
                ON plugin_installation (user_id, (manifest ->> 'name')) NULLS NOT DISTINCT
        """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_document (
            id uuid PRIMARY KEY,
            plugin uuid NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
            collection text NOT NULL,
            key text NOT NULL,
            repository text,
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            document jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT plugin_document_one_scope CHECK ((repository IS NULL) <> (user_id IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_document_key
            ON plugin_document (plugin, collection, repository, user_id, key) NULLS NOT DISTINCT
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_thread (
            id uuid PRIMARY KEY,
            installation uuid NOT NULL REFERENCES plugin_installation (id) ON DELETE CASCADE,
            thread_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS plugin_thread_thread_id ON plugin_thread (thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS plugin_thread_installation ON plugin_thread (installation)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_room_update (
            id uuid PRIMARY KEY,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            plugin uuid NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
            room text NOT NULL,
            repository text,
            user_id uuid REFERENCES users (id) ON DELETE CASCADE,
            payload bytea NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
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


def downgrade() -> None:
    raise NotImplementedError
