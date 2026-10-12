"""Per-person OAuth credentials for third-party providers.

One row per person and provider, keyed by the person rather than a GitHub login,
since logins are mutable. Tokens and any per-person client secret are encrypted
by the application (TOKEN_ENCRYPTION_KEY) before they reach this table.
"""

from alembic import op

revision = "f8280ead09c6"
down_revision = ["1a27b64154a3", "243390dbd39e", "f7b59c5091a8", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_oauth_credential (
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            provider text NOT NULL,
            encrypted_access_token text NOT NULL,
            encrypted_refresh_token text,
            access_token_expires_at timestamptz,
            client_id text NOT NULL,
            encrypted_client_secret text,
            token_endpoint text NOT NULL,
            account_email text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (user_id, provider)
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
