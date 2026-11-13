"""Let a managed tools card name no kit.

A connect card names the LMT servers it offers; the kit it came from is optional, so
cards can be stored once Robin reads a person's services from an instance-wide kit
instead of a per-environment one. Existing cards keep their kit.
"""

from alembic import op

revision = "8ce50ebc1299"
down_revision = ["cff3e5d43f43", "147a16ec2b4a", "c04ec5f250f1"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE managed_tools_card ALTER COLUMN gateway DROP NOT NULL")


def downgrade() -> None:
    # Cards without a kit cannot be kept once every card must name one.
    op.execute("DELETE FROM managed_tools_card WHERE gateway IS NULL")
    op.execute("ALTER TABLE managed_tools_card ALTER COLUMN gateway SET NOT NULL")
