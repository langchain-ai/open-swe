"""Incident state moved out of the LangGraph Store.

``incident_policy`` holds the one policy; a unique index on ``(true)`` keeps it
to one row. ``incident`` holds one row per incident, unique on ``external_id``,
the id dashboard links and Slack messages carry, derived from the Slack team
(``slack_team_id``) and channel: the incident record in ``incident``, and
beside it its latest agent ``report``, its curated ``history`` and its
``postmortem`` Markdown, each NULL until first written. Each JSONB column keeps
its record's Store shape. ``incident_command`` holds responder command receipts
for an incident, unique on ``claim_id``; ``copied_from_store``
marks one the Store import copied, which the import deletes again if a replica
of the previous release deletes it from the Store. ``created_by`` and
``updated_by`` name the person whose action wrote the row, and stay NULL for
the agent's own and system writes.
"""

from alembic import op

revision = "0d10512c3176"
down_revision = "1cdf9e884dad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE incident_policy (
            id uuid PRIMARY KEY,
            version integer NOT NULL,
            policy jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_policy_singleton ON incident_policy ((true))")
    op.execute("""
        CREATE TABLE incident (
            id uuid PRIMARY KEY,
            external_id text NOT NULL,
            slack_team_id text NOT NULL,
            channel_id text NOT NULL REFERENCES slack_channel (id),
            thread_id text NOT NULL,
            status text NOT NULL,
            incident jsonb NOT NULL,
            report jsonb,
            history jsonb,
            postmortem text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_external_id ON incident (external_id)")
    op.execute("CREATE INDEX incident_thread_id ON incident (thread_id)")
    op.execute("""
        CREATE TABLE incident_command (
            id uuid PRIMARY KEY,
            claim_id text NOT NULL,
            incident uuid NOT NULL REFERENCES incident (id) ON DELETE CASCADE,
            action text NOT NULL,
            content_hash text NOT NULL,
            copied_from_store boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX incident_command_claim_id ON incident_command (claim_id)")


def downgrade() -> None:
    raise NotImplementedError
