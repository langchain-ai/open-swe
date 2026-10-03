"""Search pull request titles and descriptions"""

from alembic import op

revision = "f7b59c5091a8"
down_revision = "61e8a2c88c54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pull_request ADD COLUMN body text NOT NULL DEFAULT ''")
    op.execute(
        """
        ALTER TABLE pull_request ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('english', title), 'A') ||
                setweight(to_tsvector('english', body), 'B')
            ) STORED NOT NULL
        """
    )
    op.execute("CREATE INDEX pull_request_search_idx ON pull_request USING gin (search_vector)")


def downgrade() -> None:
    raise NotImplementedError
