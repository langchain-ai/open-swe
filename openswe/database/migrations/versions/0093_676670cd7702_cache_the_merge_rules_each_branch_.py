"""Cache the merge rules each branch enforces"""

from alembic import op

revision = "676670cd7702"
down_revision = ["4a8f25fefeb8", "981024b5de47"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE branch_merge_rule (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
            branch text NOT NULL,
            requires_conversation_resolution boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX branch_merge_rule_branch ON branch_merge_rule(repository_id, branch)"
    )
    op.execute("CREATE INDEX branch_merge_rule_created_by ON branch_merge_rule(created_by)")
    op.execute("CREATE INDEX branch_merge_rule_updated_by ON branch_merge_rule(updated_by)")
    op.execute("""
        COMMENT ON TABLE branch_merge_rule IS
            'What GitHub requires before a pull request into a branch merges, read at updated_at.'
    """)


def downgrade() -> None:
    raise NotImplementedError
