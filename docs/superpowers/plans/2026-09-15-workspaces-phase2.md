# Workspaces Phase 2: Fix Wave and PostgreSQL Storage

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the whole-branch review fixes, then move workspace records and their repository and Slack-channel bindings from the LangGraph Store into PostgreSQL, where the database enforces "a repository belongs to exactly one workspace".

**Architecture:** `agent/workspaces/store.py` keeps its public surface (`Workspace`, `WorkspaceCreate`, `WorkspaceUpdate`, `WORKSPACES` with `get`, `list_all`, `create`, `apply_update`, `publish`, `remove`, `save`, the capture and refresh helpers) but the `WorkspaceStore` implementation writes rows through `agent.database.postgres.session()` instead of `TypedStore`. Three tables: `workspace` (one row per workspace, slug unique), `workspace_repository` (`repository_id` is the primary key, so one owner per repository, referencing main's `repository` table), and `workspace_slack_channel` (`channel_id` primary key). Team settings and workspace MCP connections stay in the Store, keyed by slug, unchanged. Existing Store records (`["workspaces"]` and legacy `["environments"]`) are imported once at startup after migrations, then deleted from the Store.

**Tech Stack:** SQLAlchemy 2 async with asyncpg (as `agent/github/repositories.py` and `agent/users/models.py`), Alembic raw-SQL migrations under `agent/database/migrations/versions/`, tests on the `registry_db` fixture (`TEST_ANALYTICS_POSTGRES_URI`; locally `postgresql+asyncpg://mukil@localhost:5432/open_swe_test`).

## Global Constraints

- Everything in the phase 1 plan's Global Constraints still applies (AGENTS.md conventions, `./.venv/bin/pytest`, no co-author trailer, snapshot names keep `-environment-`).
- The Pydantic `Workspace` model remains the domain and API shape; ORM rows are an implementation detail of the store module.
- Uniqueness violations surface as the same `ValueError` messages the API and tests already expect (`repository <owner/name> already belongs to workspace <slug>`, `slack channel <id> already belongs to workspace <slug>`, `a workspace must list at least one repository`).
- Tests that need workspace rows use `registry_db` (skipped without a database; CI provides one). Tests that only need routing behavior may seed through the public `WORKSPACES.create` on `registry_db`.
- `POSTGRES_URI` is already required at startup on main; this phase adds no new setting.

## Tasks

### Task 16: Fix wave from the whole-branch review plus rebase fallout

**Status: Complete.** Findings and prescribed fixes: `.superpowers/sdd/final-review.md` (Critical, Important, "Minor to fix now"). Rebase fallout: 20 tests fail because `agent/workspaces/routing.py` now raises `WorkspaceLookupError` on store failures and those tests never seed a store. Intended end state: only `repo_is_routable` (called by the GitHub webhook route) fails closed, with the route answering 503 so GitHub retries; `resolve_workspace`, `workspace_for_repo_config`, and every Slack, Linear, schedule, and dashboard caller fail soft to `default` with an error log; tests that exercise the real store path get the `fake_store` fixture. Drop `default_workspace` from `GET /me` (M10) now that main's `/me` is backed by the users table.

Landed as the fix-wave commits `db0c8d83c..00c503246` (10 commits): `fix(workspaces): key cached team settings by workspace`, `fix(workspaces): clean up legacy environment records after migration`, `fix(workspaces): validate before capture, baby-sit allowlist, 400 vs 409`, `fix(slack): let a bound channel outrank a defaulted repository`, `fix(workspaces): raise WorkspaceLookupError instead of failing open on store errors`, `fix(workspaces): fail closed only at the GitHub route`, `refactor(dashboard): drop the unread default workspace from /me`, `fix(ui): flatten the sidebar when there is one workspace`, `feat(ui): scope the review page settings to a workspace`, `chore: regenerate swagger and type the requested workspace as object`, `test(dashboard): cover the 400 on an unusable workspace name`.

### Task 17: Migration `0013_workspaces`

**Status: Complete.** `9e0efb9e0 feat(workspaces): add the workspace, workspace_repository, and workspace_slack_channel tables`.

Create `agent/database/migrations/versions/0013_workspaces.py` (`revision = "0013"`, `down_revision = "0012"`):

```sql
CREATE TABLE workspace (
    id uuid PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    prompt text NOT NULL DEFAULT '',
    setup_script text NOT NULL DEFAULT '',
    update_script text NOT NULL DEFAULT '',
    base_snapshot_id text,
    mem_bytes bigint,
    vcpus integer,
    fs_capacity_bytes bigint,
    create_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    snapshot_id text,
    snapshot_name text,
    snapshot_status text NOT NULL DEFAULT 'none' CHECK (snapshot_status IN ('none', 'capturing', 'ready', 'failed')),
    status_message text,
    snapshot_tag text,
    source_sandbox_id text,
    last_captured_at timestamptz,
    refresh_status text NOT NULL DEFAULT 'never',
    refresh_kind text,
    refresh_run_id text,
    refresh_started_at timestamptz,
    refresh_finished_at timestamptz,
    refresh_log text,
    refresh_error text,
    refresh_cron_id text,
    refresh_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
    refresh_sandbox_id text,
    created_by text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE workspace_repository (
    repository_id uuid PRIMARY KEY REFERENCES repository (id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX workspace_repository_workspace_idx ON workspace_repository (workspace_id);
CREATE TABLE workspace_slack_channel (
    channel_id text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX workspace_slack_channel_workspace_idx ON workspace_slack_channel (workspace_id);
```

`downgrade()` raises `NotImplementedError` like the neighbors. Test: `tests/workspaces/test_migration.py` on `registry_db` asserts the three tables exist and that inserting two `workspace_repository` rows for one `repository_id` raises an integrity error.

### Task 18: `WorkspaceStore` on PostgreSQL

**Status: Complete.** `830903a8a feat(workspaces): store workspaces in PostgreSQL`; the startup Store import landed separately as `444f498cb feat(workspaces): import Store records into PostgreSQL at startup`.

Add `agent/workspaces/rows.py` with mapped dataclasses `WorkspaceRow` (table `workspace`), `WorkspaceRepositoryRow`, `WorkspaceSlackChannelRow` on `agent.database.orm.Base`, plus `to_workspace(row, repos, channels) -> Workspace` and `apply_workspace(row, workspace)` converters (refresh steps and create params through JSON). Rewrite `WorkspaceStore` in `agent/workspaces/store.py`:
- `get(slug)`: select row with its repositories (join `repository.full_name`) and channels.
- `list_all()`: one query with `selectinload`, ordered by name.
- `put(slug, record)` (used by `save`, `create`, `publish`): in one `postgres.session()`, upsert the row, upsert each repo through `Repository(full_name=...).save(session)` to obtain ids, replace the `workspace_repository` and `workspace_slack_channel` rows for this workspace, and translate `IntegrityError` on `workspace_repository_pkey` / `workspace_slack_channel_pkey` into the existing `ValueError` messages by looking up the current owner. Keep `_assert_unique` as a pre-check for the friendlier message and the min-one-repo rule; the database is the guarantee.
- `delete(slug)`: delete the row (cascades).
- `owner_of_repo` / `owner_of_slack_channel`: single indexed queries; make `routing.workspace_for_repo` / `workspace_for_slack_channel` delegate to them and drop the in-memory scan (keep the 30 s list cache only for `_slug_exists`).
- Legacy import: `async def import_store_records() -> int` copies `["workspaces"]` then `["environments"]` Store records whose slug has no row, deletes each copied Store record, and is called from `agent/api/app.py` lifespan after `database.migrate()`; idempotent.

Tests: port `tests/workspaces/test_store.py` and `tests/workspaces/test_routing.py` to `registry_db`; add an import test seeding `fake_store` records and asserting rows exist and the Store is emptied.

### Task 19: Dependent tests and fixtures

**Status: Complete.** `5bda6f709 test(workspaces): run store and routing tests against PostgreSQL`; `b1fb9f465 test(workspaces): run workspace-seeding tests against PostgreSQL`; a follow-on fix discovered while porting these tests landed as `4f4837c82 fix(workspaces): resolve agent model defaults from the thread's workspace`.

Every test that creates workspaces through `fake_store` (`grep -rln "WORKSPACES.create\|WorkspaceCreate(" tests`) moves to `registry_db` (add a `workspace_db` alias fixture in `tests/conftest.py` if it reads better). Tests that only need "no workspaces" keep working because an empty table routes to `default`. Run the full set of previously touched suites with `TEST_ANALYTICS_POSTGRES_URI` set.

### Task 20: Docs and design record

Update the Storage section of `oeps/0003-workspaces.md` and `docs/INSTALLATION.md` (workspaces live in PostgreSQL with the `repository` table; Store import happens once at startup), then re-run the two-workspace end-to-end checks from Task 15 against the rebased branch and record results.
