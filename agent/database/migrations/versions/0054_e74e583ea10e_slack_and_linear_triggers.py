"""Slack and Linear automation triggers, and schedules without repositories"""

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
    # prompt instead, so an automation keeps working where it did.
    op.execute(
        """
        UPDATE automation a
        SET prompt = a.prompt || E'\n\nWork in the `' || (t.config->>'repo') || '` repository.'
        FROM automation_trigger t
        WHERE t.automation_id = a.id AND t.kind = 'schedule'
            AND coalesce(t.config->>'repo', '') <> ''
        """
    )
    op.execute("UPDATE automation_trigger SET config = config - 'repo' WHERE kind = 'schedule'")
    op.execute(
        "COMMENT ON COLUMN automation_trigger.match_key IS 'What a delivery is matched on: "
        "the lowercased repository for GitHub triggers, the channel ID for Slack triggers, "
        "and the team key for Linear triggers.'"
    )


def downgrade() -> None:
    raise NotImplementedError
