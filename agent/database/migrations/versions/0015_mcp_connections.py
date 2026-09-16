"""MCP connections, one table per scope.

A personal connection belongs to a ``users`` row, so it is deleted with the
person. A workspace one keys on the slug with no foreign key: the ``default``
workspace is allowed to have no ``workspace`` row.
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_mcp_connection (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL,
            url text NOT NULL,
            transport text NOT NULL DEFAULT 'streamable_http' CHECK (transport IN ('streamable_http', 'sse')),
            enabled boolean NOT NULL DEFAULT true,
            allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
            header_names jsonb NOT NULL DEFAULT '[]'::jsonb,
            oauth jsonb,
            encrypted_headers text NOT NULL DEFAULT '',
            encrypted_client_secret text NOT NULL DEFAULT '',
            revision text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (user_id, name)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE workspace_mcp_connection (
            id uuid PRIMARY KEY,
            workspace_slug text NOT NULL,
            name text NOT NULL,
            url text NOT NULL,
            transport text NOT NULL DEFAULT 'streamable_http' CHECK (transport IN ('streamable_http', 'sse')),
            enabled boolean NOT NULL DEFAULT true,
            allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
            header_names jsonb NOT NULL DEFAULT '[]'::jsonb,
            oauth jsonb,
            encrypted_headers text NOT NULL DEFAULT '',
            encrypted_client_secret text NOT NULL DEFAULT '',
            revision text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (workspace_slug, name)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
