from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE repository (
            key text PRIMARY KEY,
            full_name text NOT NULL,
            private boolean,
            default_branch text NOT NULL DEFAULT '',
            first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            last_activity_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request (
            repository_key text NOT NULL REFERENCES repository (key),
            number integer NOT NULL CHECK (number > 0),
            owner text NOT NULL,
            repo text NOT NULL,
            state text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'draft', 'merged', 'closed')),
            title text NOT NULL DEFAULT '',
            head_ref text NOT NULL DEFAULT '',
            base_ref text NOT NULL DEFAULT '',
            author text NOT NULL DEFAULT '',
            resolves_thread boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (repository_key, number)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request_thread (
            repository_key text NOT NULL,
            number integer NOT NULL,
            thread_id text NOT NULL,
            role text NOT NULL CHECK (role IN ('primary', 'secondary')),
            source text NOT NULL DEFAULT '',
            linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (repository_key, number, thread_id),
            FOREIGN KEY (repository_key, number)
                REFERENCES pull_request (repository_key, number) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX pull_request_thread_primary_idx
            ON pull_request_thread (repository_key, number) WHERE role = 'primary'
        """
    )

    op.execute(
        """
        CREATE INDEX pull_request_thread_thread_idx ON pull_request_thread (thread_id)
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request_review (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            repository_key text NOT NULL,
            number integer NOT NULL,
            reviewer_thread_id text NOT NULL DEFAULT '',
            github_review_id bigint,
            url text NOT NULL DEFAULT '',
            head_sha text NOT NULL DEFAULT '',
            finding_count integer,
            published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (repository_key, number)
                REFERENCES pull_request (repository_key, number) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX pull_request_review_github_idx
            ON pull_request_review (repository_key, number, github_review_id)
            WHERE github_review_id IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX pull_request_review_local_idx
            ON pull_request_review (repository_key, number, reviewer_thread_id, head_sha)
            WHERE github_review_id IS NULL
        """
    )


def downgrade() -> None:
    raise NotImplementedError
