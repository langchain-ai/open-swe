"""Personal sandbox secrets, injected by the sandbox auth proxy in their owner's private threads.

``encrypted_value`` is Fernet-encrypted with ``TOKEN_ENCRYPTION_KEY`` and never returned
by the API. ``name`` is the environment variable that holds a placeholder in the sandbox.
"""

from alembic import op

revision = "e076e37d159f"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE user_sandbox_secret (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name text NOT NULL CHECK (name ~ '^[A-Z_][A-Z0-9_]{0,63}$'),
            encrypted_value text NOT NULL CHECK (encrypted_value <> ''),
            hosts jsonb NOT NULL CHECK (jsonb_typeof(hosts) = 'array'),
            header_name text NOT NULL,
            header_format text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX user_sandbox_secret_name ON user_sandbox_secret (user_id, name)"
    )


def downgrade() -> None:
    raise NotImplementedError
