"""The sidebar's read model of every LangGraph thread the dashboard could list.

LangGraph thread search only supports JSONB containment, so the sidebar's
predicates (not an automation, not resolved, derived category, private-thread
visibility) are applied in Python after scanning and discarding rows. This table
is a projection of the LangGraph thread metadata with those predicates
precomputed as columns, so a list page becomes one indexed query. It can be
dropped and rebuilt from LangGraph at any time.

``metadata`` is the full LangGraph metadata mirror, so the read path can render
the same summary without calling LangGraph. ``seq`` advances on every write and
is the cursor a live stream resumes from. ``reader_login`` is the exclusive
reader of a review chat, which even admins cannot read.
"""

from alembic import op

revision = "c8b081491303"
down_revision = "0a80973775d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE thread_index_seq")
    op.execute(
        """
        CREATE TABLE thread_index (
            thread_id text PRIMARY KEY,
            seq bigint NOT NULL DEFAULT nextval('thread_index_seq'),
            listed boolean NOT NULL,
            participants text[] NOT NULL DEFAULT '{}',
            visibility text NOT NULL CHECK (visibility IN ('public', 'private')),
            owner_login text,
            reader_login text,
            admin_thread boolean NOT NULL DEFAULT false,
            category text NOT NULL,
            source text NOT NULL,
            schedule_id text,
            repo_full_name text,
            workspace text,
            status text NOT NULL,
            latest_run_id text,
            resolved boolean NOT NULL DEFAULT false,
            viewed boolean NOT NULL DEFAULT false,
            title text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            thread_status text NOT NULL,
            metadata jsonb NOT NULL,
            synced_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("ALTER SEQUENCE thread_index_seq OWNED BY thread_index.seq")
    op.execute(
        """
        CREATE INDEX thread_index_participants_idx ON thread_index USING gin (participants)
        WHERE listed
        """
    )
    op.execute(
        """
        CREATE INDEX thread_index_list_idx
        ON thread_index (category, resolved, updated_at DESC, thread_id DESC)
        WHERE listed
        """
    )
    op.execute(
        """
        CREATE INDEX thread_index_created_idx
        ON thread_index (category, resolved, created_at DESC, thread_id DESC)
        WHERE listed
        """
    )
    op.execute(
        """
        CREATE INDEX thread_index_repo_idx ON thread_index (repo_full_name, updated_at DESC)
        WHERE listed
        """
    )
    op.execute("CREATE INDEX thread_index_seq_idx ON thread_index (seq)")
    op.execute(
        """
        CREATE INDEX thread_index_running_idx ON thread_index (thread_id)
        WHERE status = 'running'
        """
    )


def downgrade() -> None:
    raise NotImplementedError
