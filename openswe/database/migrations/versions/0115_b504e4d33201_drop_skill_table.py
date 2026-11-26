"""Drop the skill table.

Organization and personal skills live in LangSmith Context Hub repositories
(``openswe.skill_store``); nothing reads this table since they moved, and its
rows were copied there.
"""

from alembic import op

revision = "b504e4d33201"
down_revision = "77c486984f68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE IF EXISTS skill")


def downgrade() -> None:
    raise NotImplementedError
