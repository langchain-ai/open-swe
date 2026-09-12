from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL = ("ALTER TABLE pr_projection ALTER COLUMN opening_run_id DROP NOT NULL",)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
