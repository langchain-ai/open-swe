"""Mirror pull request revisions, files and check runs"""

from alembic import op

revision = "51b34e89263b"
down_revision = ["1c56fc371df9", "31f7578d4024"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pull_request
            ADD COLUMN head_sha text NOT NULL DEFAULT '',
            ADD COLUMN base_sha text NOT NULL DEFAULT '',
            ADD COLUMN commits integer,
            ADD COLUMN author_avatar_url text NOT NULL DEFAULT '',
            ADD COLUMN labels jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN assignees jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN requested_reviewers jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN github_updated_at timestamptz,
            ADD COLUMN synced_at timestamptz,
            ADD COLUMN files_head_sha text NOT NULL DEFAULT '',
            ADD COLUMN files_base_ref text NOT NULL DEFAULT '',
            ADD COLUMN merge_base_sha text NOT NULL DEFAULT '',
            ADD COLUMN files_truncated boolean NOT NULL DEFAULT false,
            ADD COLUMN checks_head_sha text NOT NULL DEFAULT '',
            ADD COLUMN checks_synced_at timestamptz
        """
    )
    op.execute("CREATE INDEX pull_request_head_idx ON pull_request (repository_id, head_sha)")
    op.execute(
        """
        CREATE TABLE pull_request_file (
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            position integer NOT NULL CHECK (position >= 0),
            path text NOT NULL,
            previous_path text,
            status text NOT NULL,
            additions integer NOT NULL DEFAULT 0,
            deletions integer NOT NULL DEFAULT 0,
            PRIMARY KEY (pull_request_id, position)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE check_run (
            id bigint PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository (id) ON DELETE CASCADE,
            head_sha text NOT NULL,
            name text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT 'queued',
            conclusion text,
            html_url text NOT NULL DEFAULT '',
            started_at timestamptz,
            completed_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX check_run_commit_idx ON check_run (repository_id, head_sha)")


def downgrade() -> None:
    raise NotImplementedError
