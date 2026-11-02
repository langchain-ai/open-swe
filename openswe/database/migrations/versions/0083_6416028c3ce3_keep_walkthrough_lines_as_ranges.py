"""Keep walkthrough lines as ranges.

Plans now hold each chunk and Other as line ranges, read with the diff's index
of hunks, which the sandbox builds once per head. Plans stored before keep
working: they are read as ranges and get their index the next time a planner
opens them. What each person has seen becomes one set of ranges at a head,
carried with the plan; the per-line content keys it replaces are dropped, so
readers mid-walkthrough keep what they approved in their own channel only.
"""

from alembic import op

revision = "6416028c3ce3"
down_revision = ["14cf183ef936", "40e708f0898f", "9553a7e0a493"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE pull_request_walkthrough ADD COLUMN diff_index jsonb")
    op.execute(
        "COMMENT ON COLUMN pull_request_walkthrough.diff_index IS 'Every changed file and its "
        "hunks at head_sha, as the blocks of git diff -U0; NULL for plans stored before it "
        "existed.'"
    )
    op.execute("""
        CREATE TABLE review_guide_seen (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            pull_request_id uuid NOT NULL REFERENCES pull_request(id) ON DELETE CASCADE,
            head_sha text NOT NULL,
            merge_base_sha text NOT NULL,
            lines jsonb NOT NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX review_guide_seen_reader ON review_guide_seen "
        "(user_id, pull_request_id)"
    )
    op.execute(
        "COMMENT ON TABLE review_guide_seen IS 'The changed lines each person has approved on a "
        "pull request, as line ranges at one head, carried to each new head.'"
    )
    op.execute(
        "COMMENT ON COLUMN review_guide_seen.lines IS 'Per path, added (+, numbered at head_sha) "
        "and deleted (-, numbered at merge_base_sha) line ranges.'"
    )
    op.execute("DROP TABLE review_guide_seen_line")


def downgrade() -> None:
    raise NotImplementedError
