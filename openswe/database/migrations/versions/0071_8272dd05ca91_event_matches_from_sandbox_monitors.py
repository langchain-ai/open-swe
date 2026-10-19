"""Event matches from sandbox monitors"""

from alembic import op

revision = "8272dd05ca91"
down_revision = "9f867d4517fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_match ALTER COLUMN subscription_id DROP NOT NULL")
    op.execute(
        "COMMENT ON COLUMN event_match.subscription_id IS 'The event_subscription that matched; "
        "no foreign key, since a one-shot is deleted as it matches. NULL for output a sandbox "
        "monitor emitted (source sandbox), whose delivery_id names the monitor.'"
    )


def downgrade() -> None:
    raise NotImplementedError
