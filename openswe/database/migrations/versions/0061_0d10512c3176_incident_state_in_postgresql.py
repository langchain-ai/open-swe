"""Incident state moved out of the LangGraph Store.

``incident_policy`` holds the one policy; a unique index on ``(true)`` keeps it
to one row. ``incident``, ``incident_report``, ``incident_history`` and
``incident_postmortem`` each hold one row per incident, unique on
``incident_id``; each JSONB column keeps its record's Store shape.
``incident_command`` holds responder command receipts, unique on ``claim_id``;
``copied_from_store`` marks one the Store import copied, which the import
deletes again if a replica of the previous release deletes it from the Store.
"""

from alembic import op

revision = "0d10512c3176"
down_revision = ["2cd871a2607d", "fcab215aad3e", "bdc6cc9ba2a9"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE incident_policy (
            id uuid PRIMARY KEY,
            version integer NOT NULL,
            policy jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_policy_singleton ON incident_policy ((true))")
    op.execute("""
        CREATE TABLE incident (
            id uuid PRIMARY KEY,
            incident_id text NOT NULL,
            workspace_id text NOT NULL,
            channel_id text NOT NULL,
            thread_id text NOT NULL,
            status text NOT NULL,
            incident jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_incident_id ON incident (incident_id)")
    op.execute("CREATE INDEX incident_thread_id ON incident (thread_id)")
    op.execute("""
        CREATE TABLE incident_report (
            id uuid PRIMARY KEY,
            incident_id text NOT NULL,
            report jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_report_incident_id ON incident_report (incident_id)")
    op.execute("""
        CREATE TABLE incident_history (
            id uuid PRIMARY KEY,
            incident_id text NOT NULL,
            history jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_history_incident_id ON incident_history (incident_id)")
    op.execute("""
        CREATE TABLE incident_postmortem (
            id uuid PRIMARY KEY,
            incident_id text NOT NULL,
            markdown text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX incident_postmortem_incident_id ON incident_postmortem (incident_id)"
    )
    op.execute("""
        CREATE TABLE incident_command (
            id uuid PRIMARY KEY,
            claim_id text NOT NULL,
            incident_id text NOT NULL,
            action text NOT NULL,
            content_hash text NOT NULL,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_command_claim_id ON incident_command (claim_id)")


def downgrade() -> None:
    raise NotImplementedError
