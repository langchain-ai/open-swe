"""Incident state moved out of the LangGraph Store.

``incident_policy`` holds the one policy, in its only row. ``incident``,
``incident_report``, ``incident_history`` and ``incident_postmortem`` each key
on the incident id; each JSONB column keeps its record's Store shape.
``incident_command`` holds responder command receipts; ``copied_from_store``
marks one the Store import copied, which the import deletes again if a replica
of the previous release deletes it from the Store.
"""

from alembic import op

revision = "0d10512c3176"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE incident_policy (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            version integer NOT NULL,
            policy jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE incident (
            id text PRIMARY KEY,
            workspace_id text NOT NULL,
            channel_id text NOT NULL,
            thread_id text NOT NULL,
            status text NOT NULL,
            incident jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX incident_thread_id ON incident (thread_id)")
    op.execute("""
        CREATE TABLE incident_report (
            incident_id text PRIMARY KEY,
            report jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE incident_history (
            id text PRIMARY KEY,
            workspace_id text NOT NULL,
            channel_id text NOT NULL,
            history jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE incident_postmortem (
            incident_id text PRIMARY KEY,
            markdown text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE incident_command (
            id text PRIMARY KEY,
            incident_id text NOT NULL,
            action text NOT NULL,
            content_hash text NOT NULL,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)


def downgrade() -> None:
    raise NotImplementedError
