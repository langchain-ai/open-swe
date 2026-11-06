"""Consolidate cached branch configuration"""

from alembic import op

revision = "11d943df8abc"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE required_check_cache RENAME TO branch_config")
    op.execute(
        "ALTER INDEX required_check_cache_repository_branch RENAME TO branch_config_repository_branch"
    )
    op.execute(
        "ALTER TABLE branch_config ADD COLUMN requires_conversation_resolution boolean, ADD COLUMN conversation_rule_updated_at timestamptz"
    )


def downgrade() -> None:
    raise NotImplementedError
