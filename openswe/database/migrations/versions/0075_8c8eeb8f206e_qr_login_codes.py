"""Hashed, expiring, single-use QR sign-in codes belonging to their issuer."""

from alembic import op

revision = "8c8eeb8f206e"
down_revision = "87e4182be853"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE qr_login_code (
            id uuid PRIMARY KEY,
            code_hash text NOT NULL,
            expires_at timestamptz NOT NULL,
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX qr_login_code_hash ON qr_login_code (code_hash)")
    op.execute("CREATE INDEX qr_login_code_created_by ON qr_login_code (created_by)")


def downgrade() -> None:
    raise NotImplementedError
