"""Add tasks and delegations"""

from alembic import op

revision = "67623707f126"
down_revision = ["1a27b64154a3", "243390dbd39e", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE agent_task (
            id text PRIMARY KEY,
            coordinator_thread_id text NOT NULL UNIQUE,
            coordinator_role text NOT NULL DEFAULT 'coordinator'
                CHECK (coordinator_role = 'coordinator'),
            workspace text NOT NULL,
            title text NOT NULL CHECK (btrim(title) <> ''),
            acceptance_criteria jsonb NOT NULL
                CHECK (jsonb_typeof(acceptance_criteria) = 'array'
                       AND jsonb_array_length(acceptance_criteria) > 0),
            delegated boolean NOT NULL DEFAULT false,
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
            completion_evidence jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (id, coordinator_thread_id),
            CHECK (status <> 'completed' OR (
                completion_evidence IS NOT NULL
                AND jsonb_typeof(completion_evidence) = 'array'
                AND jsonb_array_length(completion_evidence) = jsonb_array_length(acceptance_criteria)
            ))
        )
    """)
    op.execute("""
        CREATE TABLE task_membership (
            thread_id text PRIMARY KEY,
            task_id text NOT NULL REFERENCES agent_task(id),
            role text NOT NULL CHECK (role IN ('coordinator', 'worker')),
            UNIQUE (task_id, thread_id, role)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX task_membership_coordinator
        ON task_membership(task_id) WHERE role = 'coordinator'
    """)
    op.execute("""
        ALTER TABLE agent_task ADD CONSTRAINT agent_task_coordinator_membership
        FOREIGN KEY (id, coordinator_thread_id, coordinator_role)
        REFERENCES task_membership(task_id, thread_id, role)
        DEFERRABLE INITIALLY DEFERRED
    """)
    op.execute("""
        CREATE TABLE task_delegation (
            id text PRIMARY KEY,
            task_id text NOT NULL,
            coordinator_thread_id text NOT NULL,
            worker_thread_id text NOT NULL UNIQUE,
            worker_role text NOT NULL DEFAULT 'worker' CHECK (worker_role = 'worker'),
            instructions text NOT NULL CHECK (btrim(instructions) <> ''),
            model text,
            effort text,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
            run_id text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (task_id, worker_thread_id),
            FOREIGN KEY (task_id, coordinator_thread_id)
                REFERENCES agent_task(id, coordinator_thread_id),
            FOREIGN KEY (task_id, worker_thread_id, worker_role)
                REFERENCES task_membership(task_id, thread_id, role)
        )
    """)
    op.execute("""
        CREATE TABLE task_worker_dispatch (
            dispatch_key text PRIMARY KEY,
            worker_thread_id text NOT NULL REFERENCES task_delegation(worker_thread_id),
            content text NOT NULL CHECK (btrim(content) <> ''),
            run_id text UNIQUE,
            settled boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE INDEX task_worker_dispatch_pending ON task_worker_dispatch(created_at, dispatch_key)
        WHERE NOT settled
    """)
    op.execute("""
        CREATE TABLE task_event (
            id text PRIMARY KEY,
            task_id text NOT NULL,
            worker_thread_id text NOT NULL,
            event_key text NOT NULL,
            kind text NOT NULL CHECK (btrim(kind) <> ''),
            content text NOT NULL,
            delivered boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (worker_thread_id, event_key),
            FOREIGN KEY (task_id, worker_thread_id)
                REFERENCES task_delegation(task_id, worker_thread_id)
        )
    """)
    op.execute("""
        CREATE INDEX task_event_pending ON task_event(task_id, created_at, id)
        WHERE NOT delivered
    """)
    op.execute("""
        CREATE TABLE task_dispatch_receipt (
            thread_id text NOT NULL REFERENCES task_membership(thread_id),
            dispatch_key text NOT NULL CHECK (btrim(dispatch_key) <> ''),
            invocation_id text NOT NULL CHECK (btrim(invocation_id) <> ''),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (thread_id, dispatch_key)
        )
    """)
    op.execute("""
        CREATE FUNCTION preserve_task_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.id <> NEW.id OR OLD.coordinator_thread_id <> NEW.coordinator_thread_id
                OR OLD.workspace <> NEW.workspace OR (OLD.delegated AND NOT NEW.delegated) THEN
                RAISE EXCEPTION 'Task coordinator, workspace, and delegation are permanent';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER preserve_task_identity BEFORE UPDATE ON agent_task
        FOR EACH ROW EXECUTE FUNCTION preserve_task_identity()
    """)
    op.execute("""
        CREATE FUNCTION preserve_task_membership() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.thread_id <> NEW.thread_id OR OLD.task_id <> NEW.task_id
                OR OLD.role <> NEW.role THEN
                RAISE EXCEPTION 'Task membership is permanent';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER preserve_task_membership BEFORE UPDATE ON task_membership
        FOR EACH ROW EXECUTE FUNCTION preserve_task_membership()
    """)
    op.execute("""
        CREATE FUNCTION mark_task_delegated() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE agent_task SET delegated = true
            WHERE id = NEW.task_id AND status = 'active';
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Only active tasks accept delegation';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER mark_task_delegated BEFORE INSERT ON task_delegation
        FOR EACH ROW EXECUTE FUNCTION mark_task_delegated()
    """)


def downgrade() -> None:
    raise NotImplementedError
