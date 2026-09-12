from agent.analytics.migrations.operations import execute_script

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SQL = "SET search_path TO open_swe, public;\n\nALTER TABLE deployment_metadata\n    ADD COLUMN IF NOT EXISTS reporting_cutover_at timestamptz;\n"


def upgrade() -> None:
    execute_script(SQL)


def downgrade() -> None:
    raise NotImplementedError
