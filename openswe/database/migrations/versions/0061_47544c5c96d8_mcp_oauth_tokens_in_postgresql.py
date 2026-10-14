"""The MCP server's OAuth state moved out of the LangGraph Store.

One table per collection FastMCP's OAuth proxy keeps (client registrations,
transactions, authorization codes, upstream tokens, refresh token metadata,
JTI mappings, consent CSRF tokens), each entry sealed with
``TOKEN_ENCRYPTION_KEY``. The proxy keys them by its own ids and no person is
behind its writes, so ``created_by`` and ``updated_by`` stay NULL.
``expires_at`` is copied out of the sealed entry so reads and the sweep of
expired rows need not decrypt; ``copied_from_store`` marks rows the Store
import wrote and any other write clears.
"""

from alembic import op

revision = "47544c5c96d8"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None

_TABLES = (
    "mcp_oauth_client",
    "mcp_oauth_transaction",
    "mcp_oauth_authorization_code",
    "mcp_oauth_upstream_token",
    "mcp_oauth_refresh_token",
    "mcp_oauth_jti_mapping",
    "mcp_oauth_consent_csrf_token",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"""
            CREATE TABLE {table} (
                id uuid PRIMARY KEY,
                key text NOT NULL,
                sealed_entry text NOT NULL,
                expires_at timestamptz,
                copied_from_store boolean NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                created_by uuid REFERENCES users (id) ON DELETE SET NULL,
                updated_by uuid REFERENCES users (id) ON DELETE SET NULL
            )
        """)
        op.execute(f"CREATE UNIQUE INDEX {table}_key ON {table} (key)")
        op.execute(f"""
            CREATE INDEX {table}_expires_at ON {table} (expires_at)
            WHERE expires_at IS NOT NULL
        """)


def downgrade() -> None:
    raise NotImplementedError
