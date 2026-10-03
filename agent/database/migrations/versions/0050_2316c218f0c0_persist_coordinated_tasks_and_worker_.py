"""Persist coordinated tasks and worker delegation"""

from alembic import op

revision = "2316c218f0c0"
down_revision = ["1a27b64154a3", "243390dbd39e", "f7b59c5091a8", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE coordinated_task (
            id uuid PRIMARY KEY,
            coordinator_id text NOT NULL UNIQUE,
            coordinator_role text NOT NULL DEFAULT 'coordinator'
                CHECK (coordinator_role = 'coordinator'),
            acceptance_criteria jsonb NOT NULL DEFAULT '[]',
            delegated boolean NOT NULL DEFAULT false,
            completed boolean NOT NULL DEFAULT false,
            assessment text NOT NULL DEFAULT '',
            UNIQUE (id, coordinator_id),
            CHECK (jsonb_typeof(acceptance_criteria) = 'array')
        )
    """)
    op.execute("""
        CREATE TABLE task_membership (
            thread_id text PRIMARY KEY,
            task_id uuid NOT NULL REFERENCES coordinated_task(id),
            role text NOT NULL CHECK (role IN ('coordinator', 'worker')),
            UNIQUE (task_id, thread_id, role)
        )
    """)
    op.execute("""
        ALTER TABLE coordinated_task ADD CONSTRAINT task_coordinator_membership
            FOREIGN KEY (id, coordinator_id, coordinator_role)
            REFERENCES task_membership(task_id, thread_id, role)
            DEFERRABLE INITIALLY DEFERRED
    """)
    op.execute("""
        CREATE UNIQUE INDEX task_single_coordinator ON task_membership(task_id)
            WHERE role = 'coordinator'
    """)
    op.execute("""
        CREATE TABLE task_delegation (
            worker_id text PRIMARY KEY REFERENCES task_membership(thread_id),
            coordinator_id text NOT NULL REFERENCES task_membership(thread_id),
            instructions text NOT NULL,
            model text,
            effort text,
            dispatched boolean NOT NULL DEFAULT false,
            task_id uuid NOT NULL,
            worker_role text NOT NULL DEFAULT 'worker' CHECK (worker_role = 'worker'),
            coordinator_role text NOT NULL DEFAULT 'coordinator'
                CHECK (coordinator_role = 'coordinator'),
            FOREIGN KEY (task_id, worker_id, worker_role)
                REFERENCES task_membership(task_id, thread_id, role),
            FOREIGN KEY (task_id, coordinator_id, coordinator_role)
                REFERENCES task_membership(task_id, thread_id, role),
            FOREIGN KEY (task_id, coordinator_id)
                REFERENCES coordinated_task(id, coordinator_id)
        );
    """)


def downgrade() -> None:
    raise NotImplementedError
