"""Reviewer findings, stored under the pull request they were raised on."""

from alembic import op

revision = "4f14a5ad381a"
down_revision = "b88822514d4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A row exists once a pull request's findings live here; reviewer threads
    # without one still hold theirs in LangGraph thread metadata and are copied
    # over on first use.
    op.execute(
        """
        CREATE TABLE pull_request_finding_state (
            pull_request_id uuid PRIMARY KEY REFERENCES pull_request (id) ON DELETE CASCADE,
            reviewer_thread_id text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request_finding (
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            id text NOT NULL,
            position integer NOT NULL,
            rank integer,
            severity text NOT NULL,
            confidence text NOT NULL,
            category text NOT NULL,
            title text NOT NULL,
            file text NOT NULL,
            start_line integer,
            end_line integer,
            side text NOT NULL,
            in_diff boolean NOT NULL,
            description text NOT NULL,
            suggestion text,
            status text NOT NULL,
            first_seen_sha text NOT NULL,
            last_confirmed_sha text NOT NULL,
            github_review_id bigint,
            github_review_run_id text,
            github_review_comment_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            github_review_thread_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            github_resolved_thread_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            github_posted_resolution_comment_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            surface_state text NOT NULL,
            last_human_reply_at text,
            last_human_reply_author text,
            last_human_reply_body text,
            last_reconciliation_note text,
            resolution_note text,
            diff_hunk text,
            fingerprint text NOT NULL,
            PRIMARY KEY (pull_request_id, id),
            UNIQUE (pull_request_id, position)
        )
        """
    )

    # author is the GitHub login as the thread shows it; author_user_id links it
    # when that login belongs to a registered user.
    op.execute(
        """
        CREATE TABLE pull_request_finding_interaction (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL,
            finding_id text NOT NULL,
            position integer NOT NULL,
            kind text,
            github_comment_id bigint,
            github_parent_comment_id bigint,
            author text,
            author_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            body text,
            created_at text,
            needs_reassessment boolean,
            FOREIGN KEY (pull_request_id, finding_id)
                REFERENCES pull_request_finding (pull_request_id, id) ON DELETE CASCADE,
            UNIQUE (pull_request_id, finding_id, position)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX pull_request_finding_interaction_author_user_idx
            ON pull_request_finding_interaction (author_user_id)
            WHERE author_user_id IS NOT NULL
        """
    )


def downgrade() -> None:
    raise NotImplementedError
