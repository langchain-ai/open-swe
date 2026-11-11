"""Add plugin document attachments.

``plugin_document_attachment`` holds the files an app keeps beside one of its
documents, such as screenshots: named per document, at most 8 MiB each, read by
whoever may read the document, and deleted with it. A file of at most 256 KiB is
kept here, in ``content``; a larger one is an object in object storage under
``object_key``, which a sweep deletes once no row names it.
"""

from alembic import op

revision = "e17463737f00"
down_revision = "655a0b3fb348"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("""
        CREATE TABLE plugin_document_attachment (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
            document uuid NOT NULL REFERENCES plugin_document (id) ON DELETE CASCADE,
            name text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$'),
            content_type text NOT NULL,
            size integer NOT NULL CHECK (size BETWEEN 1 AND 8388608),
            content bytea CHECK (content IS NULL OR (octet_length(content) = size AND size <= 262144)),
            object_key text,
            CONSTRAINT plugin_document_attachment_one_place
                CHECK ((content IS NULL) <> (object_key IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX plugin_document_attachment_name
            ON plugin_document_attachment (document, name)
    """)
    op.execute("""
        CREATE UNIQUE INDEX plugin_document_attachment_object_key
            ON plugin_document_attachment (object_key)
    """)


def downgrade() -> None:
    raise NotImplementedError
