from agent.analytics.migrations.operations import execute_script

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nALTER TABLE pr_projection ALTER COLUMN opening_run_id DROP NOT NULL;\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
