"""Slack and Linear automation triggers; schedules without repositories; no Slack destination"""

from alembic import op

revision = "e74e583ea10e"
down_revision = "f4030c2b06bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation_trigger DROP CONSTRAINT automation_trigger_kind_check")
    op.execute(
        "ALTER TABLE automation_trigger ADD CONSTRAINT automation_trigger_kind_check "
        "CHECK (kind IN ('schedule', 'github', 'slack', 'linear'))"
    )
    # Schedules stop naming the repository their runs start in; say it in the
    # prompt instead, so an automation keeps working where it did. Every
    # schedule's repository is kept, and the line is about scheduled runs only:
    # event-triggered runs keep working in their event's repository.
    op.execute(
        """
        UPDATE automation a
        SET prompt = a.prompt || E'\n\nScheduled runs work in ' || r.repos || '.'
        FROM (
            SELECT automation_id,
                string_agg(DISTINCT '`' || (config->>'repo') || '`', ', ') AS repos
            FROM automation_trigger
            WHERE kind = 'schedule' AND coalesce(config->>'repo', '') <> ''
            GROUP BY automation_id
        ) r
        WHERE r.automation_id = a.id
        """
    )
    op.execute("UPDATE automation_trigger SET config = config - 'repo' WHERE kind = 'schedule'")
    # Automations stop posting to a Slack destination themselves; a channel
    # destination becomes an instruction the run follows with its Slack tool.
    # A DM destination has no such tool and is dropped.
    op.execute(
        """
        UPDATE automation
        SET prompt = prompt || E'\n\n'
            || CASE WHEN slack_notification_mode = 'on_action'
                THEN 'When a run takes a concrete action, post'
                ELSE 'Post' END
            || ' a short summary of the outcome to <#' || upper(slack_channel_id) || '>.'
        WHERE slack_channel_id ~* '^[CG]'
        """
    )
    op.execute(
        "ALTER TABLE automation DROP COLUMN slack_channel_id, DROP COLUMN slack_notification_mode"
    )
    op.execute(
        "COMMENT ON COLUMN automation_trigger.match_key IS 'What a delivery is matched on: "
        "the lowercased repository for GitHub triggers, the channel ID for Slack triggers, "
        "and the team key for Linear triggers.'"
    )


def downgrade() -> None:
    raise NotImplementedError
