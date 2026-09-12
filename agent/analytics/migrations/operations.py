from alembic import op
from sqlalchemy.util.concurrency import await_only


def execute_script(script: str) -> None:
    context = op.get_context()
    connection = op.get_bind()
    script = script.replace("open_swe", context.opts["analytics_schema"])
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("analytics migration requires an asyncpg driver connection")
    await_only(driver_connection.execute(script))
