"""Reviewer findings, keyed by the reviewer thread that raised them."""

from alembic import op

revision = "4f14a5ad381a"
down_revision = "b88822514d4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A row exists once a thread's findings live here; threads without one still
    # hold theirs in LangGraph thread metadata and are copied over on first use.
    op.execute(
        """
        CREATE TABLE review_finding_set (
            thread_id text PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE review_finding (
            thread_id text NOT NULL REFERENCES review_finding_set (thread_id) ON DELETE CASCADE,
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
            interactions jsonb NOT NULL DEFAULT '[]'::jsonb,
            PRIMARY KEY (thread_id, id),
            UNIQUE (thread_id, position)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
