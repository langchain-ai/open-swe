"""Event log visibility"""

from alembic import op

revision = "4a8f25fefeb8"
down_revision = ["1850da6129ee", "3c9e71d2a4b8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE event_log "
        "ADD COLUMN visibility text NOT NULL DEFAULT 'public', "
        "ADD COLUMN visible_to_user_id uuid REFERENCES users (id), "
        "ADD CONSTRAINT event_log_visibility_check "
        "CHECK (visibility IN ('public', 'private', 'admin')), "
        "ADD CONSTRAINT event_log_private_user_check "
        "CHECK ((visibility = 'private') = (visible_to_user_id IS NOT NULL)), "
        "ADD CONSTRAINT event_log_admin_workspace_check "
        "CHECK (visibility <> 'admin' OR workspace_id IS NOT NULL)"
    )


def downgrade() -> None:
    raise NotImplementedError
