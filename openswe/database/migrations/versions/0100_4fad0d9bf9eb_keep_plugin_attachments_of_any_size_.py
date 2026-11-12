"""Let Postgres keep app attachments up to 8 MiB.

Without object storage, an attachment's bytes live in ``content``. 0096 capped ``content`` at
256 KiB although ``size`` allows 8 MiB, so a file between the two could be stored nowhere.
This replaces that check with one that only ties ``content`` to ``size``. It loosens the old
check, so every row already passes, and the table is small.
"""

from alembic import op

revision = "4fad0d9bf9eb"
down_revision = "3047efb4bf0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    # 0096 declared the 256 KiB check on the ``content`` column without a name. It reads
    # ``content`` and ``size``, and Postgres names a check over several columns
    # ``<table>_check``.
    op.execute("""
        ALTER TABLE plugin_document_attachment
            DROP CONSTRAINT plugin_document_attachment_check,
            ADD CONSTRAINT plugin_document_attachment_content_size
                CHECK (content IS NULL OR octet_length(content) = size)
    """)


def downgrade() -> None:
    raise NotImplementedError
