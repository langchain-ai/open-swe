# Agent Instructions

## Database access

- Use the existing async SQLAlchemy ORM patterns for application persistence: mapped domain models inheriting from `agent.database.orm.Base`, typed `Mapped[...]` columns, and `agent.database.postgres.session()`.
- Follow `agent/users/models.py` for model methods, typed queries, and atomic upserts. Use SQLAlchemy expressions against mapped models, including PostgreSQL `insert(...).on_conflict_do_update(...)`, rather than handwritten SQL.
- Do not use raw SQL, direct PostgreSQL connections, or `postgres.connection()` / `postgres.transaction()` for ordinary application reads and writes when the ORM can express the operation.
- Exceptions require a concrete justification, such as migration DDL, database infrastructure, or a PostgreSQL-specific operation that cannot reasonably be expressed through the ORM. Document the reason in the PR description; convenience or nearby legacy raw SQL is not sufficient.
- Keep mappings consistent with Alembic migrations; migrations remain the source of truth for the schema.
