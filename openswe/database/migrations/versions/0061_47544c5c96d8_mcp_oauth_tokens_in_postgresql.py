"""The MCP server's OAuth state moved out of the LangGraph Store.

``mcp_oauth_entry`` holds the entries of every collection FastMCP's OAuth proxy
keeps (client registrations, transactions, authorization codes, upstream
tokens, refresh token metadata, JTI mappings, consent CSRF tokens). ``entry``
is the whole key-value entry as a document, its secret fields each sealed with
``TOKEN_ENCRYPTION_KEY``; ``collection`` and ``key`` are generated from it, and
the check keeps out a collection the application has no spec for.
``expires_at`` repeats the entry's expiry as a plain column, since casting
text to ``timestamptz`` is not immutable enough for a generated one. The
proxy's writes have no person behind them, so ``created_by`` and
``updated_by`` stay NULL; ``copied_from_store`` marks rows the Store import
wrote and any other write clears.
"""

from alembic import op

revision = "47544c5c96d8"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mcp_oauth_entry (
            id uuid PRIMARY KEY,
            entry jsonb NOT NULL,
            collection text NOT NULL GENERATED ALWAYS AS (entry->>'collection') STORED
                CHECK (collection IN (
                    'mcp-oauth-proxy-clients',
                    'mcp-oauth-transactions',
                    'mcp-authorization-codes',
                    'mcp-upstream-tokens',
                    'mcp-refresh-tokens',
                    'mcp-jti-mappings',
                    'mcp-consent-csrf-tokens'
                )),
            key text NOT NULL GENERATED ALWAYS AS (entry->>'key') STORED,
            expires_at timestamptz,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX mcp_oauth_entry_collection_key ON mcp_oauth_entry (collection, key)"
    )
    op.execute("""
        CREATE INDEX mcp_oauth_entry_expires_at ON mcp_oauth_entry (expires_at)
        WHERE expires_at IS NOT NULL
    """)


def downgrade() -> None:
    raise NotImplementedError
