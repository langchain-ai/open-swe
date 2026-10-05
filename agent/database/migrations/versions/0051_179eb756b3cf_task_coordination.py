"""Task coordination"""

from alembic import op

revision = "179eb756b3cf"
down_revision = "f8280ead09c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE coordinated_task (
            id uuid PRIMARY KEY,
            coordinator_thread_id text NOT NULL UNIQUE,
            title text NOT NULL,
            acceptance_criteria jsonb NOT NULL,
            workspace text NOT NULL,
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
            assessment jsonb NOT NULL DEFAULT '[]'::jsonb,
            delegated boolean NOT NULL DEFAULT false,
            revision integer NOT NULL DEFAULT 1,
            UNIQUE (id, coordinator_thread_id),
            CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
            CHECK (status <> 'completed' OR jsonb_array_length(acceptance_criteria) > 0)
        )
    """)
    op.execute("""
        CREATE TABLE task_membership (
            thread_id text PRIMARY KEY,
            task_id uuid NOT NULL REFERENCES coordinated_task(id),
            role text NOT NULL CHECK (role IN ('coordinator', 'worker')),
            UNIQUE (task_id, thread_id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX task_one_coordinator ON task_membership(task_id)
            WHERE role = 'coordinator'
    """)
    op.execute("""
        ALTER TABLE coordinated_task ADD CONSTRAINT task_coordinator_membership
            FOREIGN KEY (id, coordinator_thread_id)
            REFERENCES task_membership(task_id, thread_id)
            DEFERRABLE INITIALLY DEFERRED
    """)
    op.execute("""
        CREATE TABLE task_delegation (
            worker_thread_id text PRIMARY KEY,
            task_id uuid NOT NULL,
            coordinator_thread_id text NOT NULL,
            instructions text NOT NULL,
            model text NOT NULL,
            effort text,
            launch_error text,
            cancelled boolean NOT NULL DEFAULT false,
            FOREIGN KEY (task_id, worker_thread_id)
                REFERENCES task_membership(task_id, thread_id),
            FOREIGN KEY (task_id, coordinator_thread_id)
                REFERENCES coordinated_task(id, coordinator_thread_id),
            CHECK (worker_thread_id <> coordinator_thread_id)
        )
    """)
    op.execute("CREATE INDEX task_delegation_task ON task_delegation(task_id)")


def downgrade() -> None:
    raise NotImplementedError
