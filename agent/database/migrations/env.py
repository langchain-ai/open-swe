from alembic import context


def run_migrations_online() -> None:
    connection = context.config.attributes["connection"]
    schema = context.config.attributes["schema"]
    connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}, public")
    context.configure(
        connection=connection,
        version_table_schema=schema,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
