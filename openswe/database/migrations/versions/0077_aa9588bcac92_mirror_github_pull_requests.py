"""Mirror GitHub pull requests, their conversation and their CI in PostgreSQL.

The dashboard read every pull request it showed from GitHub, one fan-out of REST
and GraphQL calls per row. Webhooks already deliver nearly all of it, so the
GitHub columns of ``pull_request`` and the ``github_*`` tables below now hold
GitHub's view of each pull request, written as deliveries arrive and refreshed
from the API for what deliveries leave out (mergeability, the review decision,
commits and changed files after a push). See ``openswe/github/mirror``.

Rows are GitHub's records, not Open SWE people's: their authors are GitHub
accounts kept as login and numeric id, the way ``pull_request.author`` is.
Every write is the system's, so ``created_by``/``updated_by`` stay NULL.

``github_check_run``, ``github_commit_status`` and ``github_workflow_run`` belong
to commits, not pull requests, and are keyed by repository and SHA: a check can
report before the pull request for its commit exists.
"""

from alembic import op

revision = "aa9588bcac92"
down_revision = "79a94ddd5d16"
branch_labels = None
depends_on = None

_AUDIT = """
    created_by uuid REFERENCES users (id) ON DELETE SET NULL,
    updated_by uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
"""
_OPEN = "state IN ('open', 'draft')"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        ALTER TABLE pull_request
            ADD COLUMN github_id bigint,
            ADD COLUMN node_id text NOT NULL DEFAULT '',
            ADD COLUMN head_sha text NOT NULL DEFAULT '',
            ADD COLUMN base_sha text NOT NULL DEFAULT '',
            ADD COLUMN mergeable boolean,
            ADD COLUMN mergeable_state text NOT NULL DEFAULT '',
            ADD COLUMN review_decision text NOT NULL DEFAULT '',
            ADD COLUMN commit_count integer,
            ADD COLUMN labels jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN assignees jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN requested_reviewers jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN requested_teams jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN github_created_at timestamptz,
            ADD COLUMN github_updated_at timestamptz,
            ADD COLUMN merged_at timestamptz,
            ADD COLUMN closed_at timestamptz,
            ADD COLUMN synced_at timestamptz,
            ADD COLUMN synced_head_sha text NOT NULL DEFAULT ''
    """)
    op.execute("""
        COMMENT ON COLUMN pull_request.github_updated_at IS
            'GitHub''s updated_at for the version stored; an older delivery never overwrites a newer one'
    """)
    op.execute("""
        COMMENT ON COLUMN pull_request.synced_at IS
            'When the pull request was last read whole from the GitHub API; NULL until it has been'
    """)
    op.execute("""
        COMMENT ON COLUMN pull_request.synced_head_sha IS
            'The head the stored commits and changed files describe'
    """)
    op.execute(
        "CREATE INDEX pull_request_open_author_idx ON pull_request "
        f"(author_github_id, github_updated_at DESC) WHERE {_OPEN}"
    )
    op.execute(
        "CREATE INDEX pull_request_open_requested_reviewers_idx ON pull_request "
        f"USING gin (requested_reviewers jsonb_path_ops) WHERE {_OPEN}"
    )
    op.execute(
        "CREATE INDEX pull_request_open_requested_teams_idx ON pull_request "
        f"USING gin (requested_teams jsonb_path_ops) WHERE {_OPEN}"
    )
    op.execute(
        "CREATE INDEX pull_request_open_head_ref_idx ON pull_request "
        f"(repository_id, head_ref) WHERE {_OPEN}"
    )
    op.execute(
        "CREATE INDEX pull_request_open_base_ref_idx ON pull_request "
        f"(repository_id, base_ref) WHERE {_OPEN}"
    )
    op.execute("CREATE INDEX pull_request_head_sha_idx ON pull_request (repository_id, head_sha)")

    op.execute("""
        ALTER TABLE repository ADD COLUMN pull_requests_swept_through timestamptz
    """)
    op.execute("""
        COMMENT ON COLUMN repository.pull_requests_swept_through IS
            'GitHub updated_at up to which the sweep has compared every pull request; NULL until the first backfill'
    """)

    op.execute(f"""
        CREATE TABLE github_review (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            github_id bigint NOT NULL,
            author_login text NOT NULL DEFAULT '',
            author_github_id bigint,
            state text NOT NULL
                CHECK (state IN ('approved', 'changes_requested', 'commented', 'dismissed')),
            body text NOT NULL DEFAULT '',
            html_url text NOT NULL DEFAULT '',
            commit_sha text NOT NULL DEFAULT '',
            submitted_at timestamptz,
            {_AUDIT}
        )
    """)
    op.execute("CREATE UNIQUE INDEX github_review_github_id_idx ON github_review (github_id)")
    op.execute("CREATE INDEX github_review_pull_request_idx ON github_review (pull_request_id)")

    op.execute(f"""
        CREATE TABLE github_review_comment (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            github_id bigint NOT NULL,
            review_github_id bigint,
            in_reply_to_github_id bigint,
            author_login text NOT NULL DEFAULT '',
            author_github_id bigint,
            author_type text NOT NULL DEFAULT '',
            body text NOT NULL DEFAULT '',
            html_url text NOT NULL DEFAULT '',
            path text NOT NULL DEFAULT '',
            diff_hunk text NOT NULL DEFAULT '',
            line integer,
            original_line integer,
            start_line integer,
            side text NOT NULL DEFAULT '',
            commit_sha text NOT NULL DEFAULT '',
            github_created_at timestamptz,
            github_updated_at timestamptz,
            {_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_review_comment_github_id_idx "
        "ON github_review_comment (github_id)"
    )
    op.execute(
        "CREATE INDEX github_review_comment_pull_request_idx "
        "ON github_review_comment (pull_request_id)"
    )

    op.execute(f"""
        CREATE TABLE github_review_thread (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            root_comment_github_id bigint NOT NULL,
            node_id text NOT NULL DEFAULT '',
            resolved boolean NOT NULL DEFAULT false,
            outdated boolean NOT NULL DEFAULT false,
            {_AUDIT}
        )
    """)
    op.execute("""
        COMMENT ON TABLE github_review_thread IS
            'An inline review thread, named by the review comment that opened it; replies point at that comment'
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_review_thread_root_idx "
        "ON github_review_thread (root_comment_github_id)"
    )
    op.execute(
        "CREATE INDEX github_review_thread_pull_request_idx "
        "ON github_review_thread (pull_request_id)"
    )

    op.execute(f"""
        CREATE TABLE github_issue_comment (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            github_id bigint NOT NULL,
            author_login text NOT NULL DEFAULT '',
            author_github_id bigint,
            author_type text NOT NULL DEFAULT '',
            body text NOT NULL DEFAULT '',
            html_url text NOT NULL DEFAULT '',
            github_created_at timestamptz,
            github_updated_at timestamptz,
            {_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_issue_comment_github_id_idx ON github_issue_comment (github_id)"
    )
    op.execute(
        "CREATE INDEX github_issue_comment_pull_request_idx "
        "ON github_issue_comment (pull_request_id)"
    )

    op.execute(f"""
        CREATE TABLE github_pull_request_commit (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            sha text NOT NULL,
            position integer NOT NULL,
            message text NOT NULL DEFAULT '',
            author_login text NOT NULL DEFAULT '',
            author_github_id bigint,
            author_type text NOT NULL DEFAULT '',
            authored_at timestamptz,
            html_url text NOT NULL DEFAULT '',
            {_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_pull_request_commit_sha_idx "
        "ON github_pull_request_commit (pull_request_id, sha)"
    )

    op.execute(f"""
        CREATE TABLE github_pull_request_file (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            path text NOT NULL,
            previous_path text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT '',
            additions integer NOT NULL DEFAULT 0,
            deletions integer NOT NULL DEFAULT 0,
            {_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_pull_request_file_path_idx "
        "ON github_pull_request_file (pull_request_id, path)"
    )

    op.execute(f"""
        CREATE TABLE github_check_run (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository (id) ON DELETE CASCADE,
            github_id bigint NOT NULL,
            head_sha text NOT NULL,
            name text NOT NULL,
            status text NOT NULL,
            conclusion text NOT NULL DEFAULT '',
            details_url text NOT NULL DEFAULT '',
            html_url text NOT NULL DEFAULT '',
            app_id bigint,
            app_slug text NOT NULL DEFAULT '',
            check_suite_id bigint,
            started_at timestamptz,
            completed_at timestamptz,
            {_AUDIT}
        )
    """)
    op.execute("CREATE UNIQUE INDEX github_check_run_github_id_idx ON github_check_run (github_id)")
    op.execute(
        "CREATE INDEX github_check_run_head_idx ON github_check_run (repository_id, head_sha)"
    )

    op.execute(f"""
        CREATE TABLE github_commit_status (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository (id) ON DELETE CASCADE,
            sha text NOT NULL,
            context text NOT NULL,
            state text NOT NULL CHECK (state IN ('error', 'failure', 'pending', 'success')),
            description text NOT NULL DEFAULT '',
            target_url text NOT NULL DEFAULT '',
            github_updated_at timestamptz,
            {_AUDIT}
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_commit_status_context_idx "
        "ON github_commit_status (repository_id, sha, context)"
    )

    op.execute(f"""
        CREATE TABLE github_workflow_run (
            id uuid PRIMARY KEY,
            repository_id uuid NOT NULL REFERENCES repository (id) ON DELETE CASCADE,
            github_id bigint NOT NULL,
            head_sha text NOT NULL,
            workflow_id bigint NOT NULL,
            check_suite_id bigint NOT NULL,
            event text NOT NULL DEFAULT '',
            {_AUDIT}
        )
    """)
    op.execute("""
        COMMENT ON TABLE github_workflow_run IS
            'Actions runs, kept only to tell which cancelled check runs a later run of the same workflow superseded'
    """)
    op.execute(
        "CREATE UNIQUE INDEX github_workflow_run_github_id_idx ON github_workflow_run (github_id)"
    )
    op.execute(
        "CREATE INDEX github_workflow_run_head_idx ON github_workflow_run (repository_id, head_sha)"
    )


def downgrade() -> None:
    raise NotImplementedError
