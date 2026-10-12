"""Track author DM review cards"""

import sqlalchemy as sa
from alembic import op

revision = "52fab62a7608"
down_revision = ["12b3a8ca6a0c", "c8e1ce5e9cbe", "80aa2c1128ad"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("slack_dm_channel_id", "slack_dm_message_ts"):
        op.add_column(
            "human_review_request", sa.Column(name, sa.Text(), server_default="", nullable=False)
        )


def downgrade() -> None:
    raise NotImplementedError
