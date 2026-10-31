"""Allow LangSmith login identities"""

from alembic import op

revision = "af406b7c5892"
down_revision = "e0605414666c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_identity DROP CONSTRAINT user_identity_provider_check")
    op.execute(
        "ALTER TABLE user_identity ADD CONSTRAINT user_identity_provider_check "
        "CHECK (provider IN ('github', 'slack', 'langsmith'))"
    )


def downgrade() -> None:
    raise NotImplementedError
