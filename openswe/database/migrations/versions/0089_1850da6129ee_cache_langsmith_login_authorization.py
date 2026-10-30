"""Cache LangSmith login authorization"""

import sqlalchemy as sa
from alembic import op

revision = "1850da6129ee"
down_revision = "af406b7c5892"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_oauth_credential", sa.Column("login_authorized_until", sa.DateTime(timezone=True))
    )
    op.add_column("user_oauth_credential", sa.Column("login_authorized_org_id", sa.Text()))


def downgrade() -> None:
    raise NotImplementedError
