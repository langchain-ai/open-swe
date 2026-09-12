from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SQL = (
    "ALTER TABLE deployment_metadata\n    ADD COLUMN IF NOT EXISTS reporting_cutover_at timestamptz",
)


def upgrade() -> None:
    for statement in SQL:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
