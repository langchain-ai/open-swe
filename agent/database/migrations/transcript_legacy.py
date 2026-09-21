"""Recognize the preview transcript schema before upgrading it."""

from alembic import op
from sqlalchemy import Connection, text

TABLES = (
    "thread",
    "thread_event",
    "thread_command_receipt",
    "thread_turn",
    "thread_message",
    "thread_tool_call",
    "thread_turn_checkpoint",
    "thread_attachment",
    "thread_tool_output",
)


def schema_definition(conn: Connection) -> dict[str, list[str]]:
    definitions: dict[str, list[str]] = {}
    queries = {
        "columns": """
            SELECT c.relname, concat_ws('|', a.attname,
                format_type(a.atttypid, a.atttypmod), a.attnotnull::text,
                coalesce(pg_get_expr(d.adbin, d.adrelid), ''))
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
            WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)
                AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY c.relname, a.attname
        """,
        "constraints": """
            SELECT c.relname, pg_get_constraintdef(k.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_constraint k ON k.conrelid = c.oid
            WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)
                AND k.contype != 'n'
            ORDER BY c.relname, pg_get_constraintdef(k.oid)
        """,
        "indexes": """
            SELECT tablename, replace(indexdef, ' ON ' || schemaname || '.', ' ON ')
            FROM pg_indexes WHERE schemaname = current_schema()
                AND tablename = ANY(:tables)
            ORDER BY tablename, indexdef
        """,
        "triggers": """
            SELECT c.relname, pg_get_triggerdef(t.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_trigger t ON t.tgrelid = c.oid
            WHERE n.nspname = current_schema() AND c.relname = ANY(:tables)
                AND NOT t.tgisinternal
            ORDER BY c.relname, t.tgname
        """,
    }
    for kind, query in queries.items():
        for table, definition in conn.execute(text(query), {"tables": list(TABLES)}):
            definitions.setdefault(f"{table}.{kind}", []).append(str(definition))
    return definitions


LEGACY_DEFINITION: dict[str, list[str]] = {
    "thread.columns": [
        "active_run_id|text|false|",
        "created_at|timestamp with time zone|true|clock_timestamp()",
        "kind|text|true|'agent'::text",
        "metadata|jsonb|true|'{}'::jsonb",
        "status|text|true|'idle'::text",
        "thread_id|text|true|",
        "title|text|false|",
        "updated_at|timestamp with time zone|true|clock_timestamp()",
        "version|bigint|true|0",
    ],
    "thread.constraints": [
        "CHECK ((status = ANY (ARRAY['idle'::text, 'running'::text, 'error'::text])))",
        "PRIMARY KEY (thread_id)",
    ],
    "thread.indexes": ["CREATE UNIQUE INDEX thread_pkey ON thread USING btree (thread_id)"],
    "thread_command_receipt.columns": [
        "accepted_at|timestamp with time zone|true|clock_timestamp()",
        "command_id|text|true|",
        "error|text|false|",
        "result_version|bigint|false|",
        "status|text|true|",
        "thread_id|text|true|",
    ],
    "thread_command_receipt.constraints": [
        "CHECK ((status = ANY (ARRAY['accepted'::text, 'rejected'::text])))",
        "PRIMARY KEY (command_id)",
    ],
    "thread_command_receipt.indexes": [
        "CREATE UNIQUE INDEX thread_command_receipt_pkey ON "
        "thread_command_receipt USING btree (command_id)"
    ],
    "thread_event.columns": [
        "actor_kind|text|true|",
        "command_id|text|false|",
        "event_id|uuid|true|",
        "event_type|text|true|",
        "occurred_at|timestamp with time zone|true|clock_timestamp()",
        "payload|jsonb|true|",
        "run_id|text|false|",
        "schema_version|smallint|true|1",
        "thread_id|text|true|",
        "turn_id|uuid|false|",
        "version|bigint|true|",
    ],
    "thread_event.constraints": [
        "CHECK ((actor_kind = ANY (ARRAY['user'::text, 'agent'::text, 'system'::text])))",
        "FOREIGN KEY (thread_id) REFERENCES thread(thread_id) ON DELETE CASCADE",
        "PRIMARY KEY (thread_id, version)",
        "UNIQUE (event_id)",
    ],
    "thread_event.indexes": [
        "CREATE INDEX thread_event_run_idx ON thread_event USING btree (run_id) "
        "WHERE (run_id IS NOT NULL)",
        "CREATE UNIQUE INDEX thread_event_event_id_key ON thread_event USING btree (event_id)",
        "CREATE UNIQUE INDEX thread_event_pkey ON thread_event USING btree (thread_id, version)",
    ],
    "thread_message.columns": [
        "created_at|timestamp with time zone|true|",
        "images|jsonb|false|",
        "message_id|text|true|",
        "namespace|text[]|true|'{}'::text[]",
        "reasoning|text|true|''::text",
        "role|text|true|",
        "sender|jsonb|false|",
        "streaming|boolean|true|false",
        "text|text|true|''::text",
        "thread_id|text|true|",
        "turn_id|uuid|true|",
        "version|bigint|true|",
    ],
    "thread_message.constraints": [
        "CHECK ((role = ANY (ARRAY['human'::text, 'ai'::text])))",
        "FOREIGN KEY (thread_id) REFERENCES thread(thread_id) ON DELETE CASCADE",
        "PRIMARY KEY (message_id)",
    ],
    "thread_message.indexes": [
        "CREATE INDEX thread_message_thread_idx ON thread_message USING btree "
        "(thread_id, created_at, message_id)",
        "CREATE UNIQUE INDEX thread_message_pkey ON thread_message USING btree (message_id)",
    ],
    "thread_tool_call.columns": [
        "ended_at|timestamp with time zone|false|",
        "input|jsonb|true|",
        "message_id|text|false|",
        "name|text|true|",
        "namespace|text[]|true|'{}'::text[]",
        "output|text|false|",
        "output_preview|text|false|",
        "output_truncated|boolean|true|false",
        "started_at|timestamp with time zone|true|",
        "status|text|true|",
        "thread_id|text|true|",
        "tool_call_id|text|true|",
        "turn_id|uuid|true|",
        "version|bigint|true|",
    ],
    "thread_tool_call.constraints": [
        "CHECK ((status = ANY (ARRAY['in_progress'::text, 'completed'::text, 'error'::text])))",
        "FOREIGN KEY (thread_id) REFERENCES thread(thread_id) ON DELETE CASCADE",
        "PRIMARY KEY (tool_call_id)",
    ],
    "thread_tool_call.indexes": [
        "CREATE INDEX thread_tool_call_thread_idx ON thread_tool_call USING "
        "btree (thread_id, started_at, tool_call_id)",
        "CREATE UNIQUE INDEX thread_tool_call_pkey ON thread_tool_call USING btree (tool_call_id)",
    ],
    "thread_turn.columns": [
        "base_commit|text|false|",
        "changed_files|jsonb|false|",
        "completed_at|timestamp with time zone|false|",
        "error|text|false|",
        "head_commit|text|false|",
        "requested_at|timestamp with time zone|true|",
        "run_id|text|false|",
        "started_at|timestamp with time zone|false|",
        "state|text|true|",
        "thread_id|text|true|",
        "turn_id|uuid|true|",
    ],
    "thread_turn.constraints": [
        "CHECK ((state = ANY (ARRAY['requested'::text, 'running'::text, "
        "'completed'::text, 'failed'::text, 'interrupted'::text])))",
        "FOREIGN KEY (thread_id) REFERENCES thread(thread_id) ON DELETE CASCADE",
        "PRIMARY KEY (turn_id)",
    ],
    "thread_turn.indexes": [
        "CREATE INDEX thread_turn_thread_idx ON thread_turn USING btree "
        "(thread_id, requested_at, turn_id)",
        "CREATE UNIQUE INDEX thread_turn_pkey ON thread_turn USING btree (turn_id)",
    ],
}


def prepare_empty_legacy_schema() -> None:
    conn = op.get_bind()
    actual = schema_definition(conn)
    if not actual:
        return
    if actual != LEGACY_DEFINITION:
        differences = sorted(
            key
            for key in actual.keys() | LEGACY_DEFINITION.keys()
            if actual.get(key) != LEGACY_DEFINITION.get(key)
        )
        raise RuntimeError(f"Unrecognized transcript schema: {', '.join(differences)}")
    schema = conn.execute(text("SELECT current_schema()")).scalar_one()
    quote = conn.dialect.identifier_preparer.quote
    tables = (
        "thread_message",
        "thread_tool_call",
        "thread_turn",
        "thread_command_receipt",
        "thread_event",
        "thread",
    )
    qualified = [f"{quote(schema)}.{quote(table)}" for table in tables]
    op.execute(f"LOCK TABLE {', '.join(qualified)} IN ACCESS EXCLUSIVE MODE")
    if schema_definition(conn) != LEGACY_DEFINITION:
        raise RuntimeError("Transcript schema changed while acquiring migration locks")
    for table in qualified:
        if conn.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar_one():
            raise RuntimeError("Cannot replace a populated legacy transcript schema")
    # RESTRICT protects dependencies outside these six empty tables.
    for table in qualified:
        op.execute(f"DROP TABLE {table} RESTRICT")
