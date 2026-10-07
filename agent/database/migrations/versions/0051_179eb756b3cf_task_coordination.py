"""Task coordination"""

from alembic import op

revision = "179eb756b3cf"
down_revision = "e74e583ea10e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE task (
            id uuid PRIMARY KEY,
            coordinator_thread_id text UNIQUE,
            title text NOT NULL,
            workspace_id uuid NOT NULL REFERENCES workspace(id),
            delegated boolean NOT NULL DEFAULT false,
            UNIQUE (id, coordinator_thread_id)
        )
    """)
    op.execute("""
        CREATE TABLE task_membership (
            thread_id text PRIMARY KEY,
            task_id uuid NOT NULL REFERENCES task(id),
            role text NOT NULL CHECK (role IN ('coordinator', 'worker')),
            UNIQUE (task_id, thread_id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX task_one_coordinator ON task_membership(task_id)
            WHERE role = 'coordinator'
    """)
    op.execute("""
        ALTER TABLE task ADD CONSTRAINT task_coordinator_membership
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
                REFERENCES task(id, coordinator_thread_id),
            CHECK (worker_thread_id <> coordinator_thread_id)
        )
    """)
    op.execute("CREATE INDEX task_delegation_task ON task_delegation(task_id)")


def downgrade() -> None:
    raise NotImplementedError
