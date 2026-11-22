"""Fold incident channels into listening channel rules.

A channel name rule makes every public internal channel whose name starts with its
prefix a listening channel, joins new ones and can start a kickoff run there. An
enabled incident policy becomes the rule for its prefix, with an investigation as
its kickoff. The incident tables stay, unread, so past reports, postmortems, evidence
and policy settings remain recoverable.
"""

import sqlalchemy as sa
from alembic import op

revision = "41745a231a10"
down_revision = ["6523e182da30", "c9501de19ea1"]
branch_labels = None
depends_on = None

_INCIDENT_PROMPT = (
    "This is an incident channel. Answer questions directed at you or about the "
    "investigation, and share findings that help responders; stay quiet during "
    "responders' own coordination."
)
_INCIDENT_KICKOFF = (
    "Investigate the incident: identify what is broken and who is affected, look for "
    "the likely cause, and check whether it has happened before."
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE integration_slack_channel_rule (
            id uuid PRIMARY KEY,
            prefix text NOT NULL,
            prompt text NOT NULL DEFAULT '',
            kickoff text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX integration_slack_channel_rule_prefix "
        "ON integration_slack_channel_rule (prefix)"
    )
    op.get_bind().execute(
        sa.text("""
            INSERT INTO integration_slack_channel_rule
                (id, prefix, prompt, kickoff, created_by, updated_by)
            SELECT id, policy->>'channel_prefix', :prompt, :kickoff, created_by, updated_by
            FROM incident_policy
            WHERE (policy->>'enabled')::boolean AND coalesce(policy->>'channel_prefix', '') <> ''
        """),
        {"prompt": _INCIDENT_PROMPT, "kickoff": _INCIDENT_KICKOFF},
    )


def downgrade() -> None:
    raise NotImplementedError
