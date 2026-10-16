"""Who assigned each Notion task that waits on design approval.

Replaces the ``notion_pending_assigner`` Store namespace. The assigner is a
Notion user, who may have no Open SWE account, so it stays a Notion user id.
Rows are written from Notion webhooks, so ``created_by`` and ``updated_by``
stay NULL.
"""

from alembic import op

revision = "3a30a333c2e6"
down_revision = "c8d8a5c65df3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notion_pending_assigner (
            id uuid PRIMARY KEY,
            notion_page_id text NOT NULL,
            notion_user_id text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX notion_pending_assigner_notion_page_id "
        "ON notion_pending_assigner (notion_page_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
