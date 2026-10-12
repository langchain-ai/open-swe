"""Comment review guide columns"""

from alembic import op

revision = "1b0248b89a95"
down_revision = "c456ff969e7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "COMMENT ON TABLE review_guide_session IS 'One Slack code channel walking one person "
        "through one pull request, a chunk at a time.'",
        "COMMENT ON COLUMN review_guide_session.thread_id IS 'LangGraph thread the review-guide "
        "graph runs on; also the code channel''s session id.'",
        "COMMENT ON COLUMN review_guide_session.user_id IS 'users.id of the reader being walked "
        "through.'",
        "COMMENT ON COLUMN review_guide_session.slack_channel_id IS 'The code channel.'",
        "COMMENT ON COLUMN review_guide_session.workspace_slug IS 'Sandbox workspace the guide "
        "boots from; NULL is the default image.'",
        "COMMENT ON COLUMN review_guide_session.mode IS 'reviewer ends by approving on GitHub; "
        "author ends by marking a draft ready.'",
        "COMMENT ON COLUMN review_guide_session.walkthrough IS 'Walk state for the head it was "
        "built at: chunks shown, queued, approved or skipped, and the lines sent to Other.'",
        "COMMENT ON COLUMN review_guide_session.summary_message_ts IS 'Slack ts of the progress "
        "message edited in place; empty before it is posted.'",
        "COMMENT ON COLUMN review_guide_session.paused_message_ts IS 'Slack ts of the note that "
        "the pull request changed, with its Continue button; empty when not paused.'",
        "COMMENT ON COLUMN review_guide_session.closed_at IS 'When the walkthrough ended or its "
        "channel was archived; NULL while open.'",
        "COMMENT ON TABLE review_guide_seen_line IS 'Changed lines each person has approved on a "
        "pull request, by content, so a rebase or force-push never shows them again.'",
        "COMMENT ON COLUMN review_guide_seen_line.line_key IS 'sha256 hex of path, sign (+ or -) "
        "and line text, NUL-separated: the line by content, not position or commit.'",
        "COMMENT ON COLUMN review_guide_seen_line.seen_count IS 'How many copies of that exact "
        "line were approved; identical lines, such as a closing brace, each count once.'",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError
