"""Plugin repository grants, chosen by each person rather than at install.

``plugin_repository_grant`` is one repository a person shared with one
installation of a plugin, by picking it when the plugin asked. A plugin reaches
a repository for someone only when they shared it and can still read it, so an
install for everyone reaches nothing until each person picks. The installation's
``repositories`` move here as grants from whoever installed it.
"""

from alembic import op

revision = "90b7fdb855a7"
down_revision = "0001ba60eee0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every statement can run again: a preview database may keep these tables after
    # forgetting it stamped them, when its branch left the preview tree for a while.
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_repository_grant (
            id uuid PRIMARY KEY,
            installation uuid NOT NULL REFERENCES plugin_installation (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            repository text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS plugin_repository_grant_key
            ON plugin_repository_grant (installation, user_id, repository)
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                    AND table_name = 'plugin_installation'
                    AND column_name = 'repositories'
            ) THEN
                INSERT INTO plugin_repository_grant (id, installation, user_id, repository, created_by, updated_by)
                SELECT gen_random_uuid(), i.id, owner, repository, owner, owner
                FROM plugin_installation i
                CROSS JOIN LATERAL (SELECT COALESCE(i.user_id, i.created_by) AS owner) o
                CROSS JOIN LATERAL unnest(i.repositories) AS repository
                WHERE owner IS NOT NULL
                ON CONFLICT DO NOTHING;
                ALTER TABLE plugin_installation DROP COLUMN repositories;
            END IF;
        END $$
    """)


def downgrade() -> None:
    raise NotImplementedError
