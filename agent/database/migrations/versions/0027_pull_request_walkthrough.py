"""The review scout's reading order for a pull request."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One walkthrough per pull request: each head replaces the previous one, and
    # the page shows it only while head_sha is still the PR's head.
    op.execute(
        """
        CREATE TABLE pull_request_walkthrough (
            pull_request_id uuid PRIMARY KEY REFERENCES pull_request (id) ON DELETE CASCADE,
            head_sha text NOT NULL,
            merge_base_sha text NOT NULL,
            scout_thread_id text NOT NULL DEFAULT '',
            generated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request_walkthrough_step (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL
                REFERENCES pull_request_walkthrough (pull_request_id) ON DELETE CASCADE,
            position integer NOT NULL,
            title text NOT NULL,
            summary text NOT NULL DEFAULT '',
            is_other boolean NOT NULL DEFAULT false
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX pull_request_walkthrough_step_position_idx
            ON pull_request_walkthrough_step (pull_request_id, position)
        """
    )

    # Line ranges are inclusive [start, end] pairs: added lines in head
    # numbering, deleted lines in merge-base numbering.
    op.execute(
        """
        CREATE TABLE pull_request_walkthrough_file (
            id uuid PRIMARY KEY,
            step_id uuid NOT NULL
                REFERENCES pull_request_walkthrough_step (id) ON DELETE CASCADE,
            path text NOT NULL,
            added_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
            deleted_lines jsonb NOT NULL DEFAULT '[]'::jsonb
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX pull_request_walkthrough_file_path_idx
            ON pull_request_walkthrough_file (step_id, path)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
