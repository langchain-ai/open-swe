"""Persist incident records in Postgres"""

from alembic import op

revision = "3fcc1f9828db"
down_revision = ["6f06bbadfc02", "a823229a905f"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE incident_record (
            kind text NOT NULL CHECK (kind IN (
                'policies', 'incidents', 'reports', 'commands', 'history', 'summaries'
            )),
            key text NOT NULL,
            payload jsonb NOT NULL,
            PRIMARY KEY (kind, key)
        )
        """
    )
    op.execute("CREATE INDEX incident_record_payload_idx ON incident_record USING gin (payload)")


def downgrade() -> None:
    raise NotImplementedError
