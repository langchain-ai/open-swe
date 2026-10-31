"""Apps published as versions, and installed per person.

An app (``plugin``) now belongs to whoever published it (``owner``), under a
``name``, rather than to a GitHub repository; where a version was built is its
``origin``, kept only to show. A ``listed`` app is one anyone may add to their
own web UI; an unlisted one only its owner.

``plugin_version`` is one immutable build of an app: its manifest, read from
inside the bundle, and the bundle. One with ``preview_of`` is the preview a
thread's agent is building there, not a release.

``plugin_installation`` is one person adding an app to their web UI, pinned to
a version; nobody installs for anyone else, so installs for everyone move to
whoever made them. One with ``preview_of`` runs that thread's preview beside
it, outside the sidebar.

The apps installed so far came from GitHub, where anyone could install them,
so they are listed; each install's build becomes a version of its app.
"""

import sqlalchemy as sa
from alembic import op

revision = "2ded63c8e8ee"
down_revision = "64588d50fce1"
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
    # Run again over tables it already moved (see 0082), only the idempotent steps repeat.
    moving = _has_column("plugin_installation", "manifest")
    if moving:
        _move_installs()
    _version_apps(moving)


def _move_installs() -> None:
    # Installs for everyone become their installer's own, unless they already have one.
    op.execute("""
        UPDATE plugin_installation i SET user_id = i.created_by
        WHERE i.user_id IS NULL AND i.created_by IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM plugin_installation mine
            WHERE mine.user_id = i.created_by AND mine.manifest ->> 'name' = i.manifest ->> 'name'
        )
    """)
    op.execute("DELETE FROM plugin_installation WHERE user_id IS NULL")
    op.execute("""
        ALTER TABLE plugin
            ADD COLUMN IF NOT EXISTS owner uuid REFERENCES users (id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS name text,
            ADD COLUMN IF NOT EXISTS listed boolean NOT NULL DEFAULT false
    """)
    op.execute("""
        UPDATE plugin p SET owner = first.user_id, name = first.name, listed = true
        FROM (
            SELECT DISTINCT ON (plugin) plugin, user_id, manifest ->> 'name' AS name
            FROM plugin_installation ORDER BY plugin, created_at
        ) first
        WHERE first.plugin = p.id AND p.owner IS NULL
    """)
    op.execute("DELETE FROM plugin WHERE owner IS NULL")
    # Two sources one person installed under the same name keep apart.
    op.execute("""
        UPDATE plugin p SET name = p.name || '-' || left(p.id::text, 4)
        FROM (
            SELECT id, row_number() OVER (PARTITION BY owner, name ORDER BY created_at) AS nth
            FROM plugin
        ) ranked
        WHERE ranked.id = p.id AND ranked.nth > 1
    """)


def _version_apps(moving: bool) -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_version (
            id uuid PRIMARY KEY,
            plugin uuid NOT NULL REFERENCES plugin (id) ON DELETE CASCADE,
            number integer NOT NULL,
            manifest jsonb NOT NULL,
            bundle uuid NOT NULL REFERENCES plugin_bundle (id),
            origin jsonb NOT NULL DEFAULT '{}',
            preview_of text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_version_number
            ON plugin_version (plugin, number)
    """)
    if moving:
        _version_installs()
    op.execute("""
        ALTER TABLE plugin_installation
            ADD COLUMN IF NOT EXISTS version uuid REFERENCES plugin_version (id),
            ADD COLUMN IF NOT EXISTS preview_of text
    """)
    op.execute("UPDATE plugin_installation SET version = id WHERE version IS NULL")
    op.execute("DROP INDEX IF EXISTS plugin_installation_name")
    op.execute("""
        ALTER TABLE plugin_installation
            ALTER COLUMN version SET NOT NULL,
            ALTER COLUMN user_id SET NOT NULL,
            DROP COLUMN IF EXISTS ref,
            DROP COLUMN IF EXISTS commit_sha,
            DROP COLUMN IF EXISTS manifest,
            DROP COLUMN IF EXISTS bundle
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_installation_app
            ON plugin_installation (user_id, plugin) WHERE preview_of IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_installation_preview
            ON plugin_installation (preview_of) WHERE preview_of IS NOT NULL
    """)

    op.execute("DROP INDEX IF EXISTS plugin_source")
    op.execute("""
        ALTER TABLE plugin
            ALTER COLUMN owner SET NOT NULL,
            ALTER COLUMN name SET NOT NULL,
            DROP COLUMN IF EXISTS repository,
            DROP COLUMN IF EXISTS path
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS plugin_name ON plugin (owner, name)")


def _version_installs() -> None:
    # Each install's build is a version of its app, under the install's own id.
    op.execute("""
        INSERT INTO plugin_version
            (id, plugin, number, manifest, bundle, origin, created_at, created_by, updated_by)
        SELECT
            i.id, i.plugin,
            row_number() OVER (PARTITION BY i.plugin ORDER BY i.created_at),
            i.manifest, i.bundle,
            jsonb_build_object(
                'repository', p.repository, 'path', p.path, 'ref', i.ref, 'commit', i.commit_sha
            ),
            i.created_at, i.created_by, i.created_by
        FROM plugin_installation i JOIN plugin p ON p.id = i.plugin
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    raise NotImplementedError
