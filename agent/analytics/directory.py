"""Protected analytics identity and repository directories."""

from uuid import UUID, uuid4

from sqlalchemy import text

from agent.analytics.capture import fail_soft
from agent.analytics.database import configured, transaction, workspace_id
from agent.analytics.identity import opaque_id, opaque_person
from agent.config import ENV


@fail_soft
async def upsert_person(
    *,
    provider: str,
    immutable_person_key: str | int | None,
    github_login: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    team_key: str | None = None,
) -> None:
    if provider == "github":
        await resolve_person(
            immutable_person_key=immutable_person_key,
            github_login=github_login,
            display_name=display_name,
            email=email,
            team_key=team_key,
        )
        return
    person_id = opaque_person(provider, immutable_person_key)
    if person_id is None:
        return
    async with transaction() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO identity_directory (
                    workspace_id, person_id, github_login, display_name, email, team_id,
                    anonymize_after
                ) VALUES (
                    :workspace_id, :person_id, :github_login, :display_name, :email, :team_id,
                    clock_timestamp() + (:months * interval '1 month')
                ) ON CONFLICT (workspace_id, person_id) DO UPDATE SET
                    github_login = COALESCE(EXCLUDED.github_login, identity_directory.github_login),
                    display_name = COALESCE(EXCLUDED.display_name, identity_directory.display_name),
                    email = COALESCE(EXCLUDED.email, identity_directory.email),
                    team_id = COALESCE(EXCLUDED.team_id, identity_directory.team_id),
                    anonymize_after = EXCLUDED.anonymize_after,
                    updated_at = clock_timestamp()
                """
            ),
            {
                "workspace_id": workspace_id(),
                "person_id": person_id,
                "github_login": github_login,
                "display_name": display_name,
                "email": email,
                "team_id": opaque_id("team", team_key),
                "months": ENV.ANALYTICS_PERSON_MONTHS.get_int(13),
            },
        )


@fail_soft
async def upsert_model(provider_model_id: str | None) -> None:
    model_id = opaque_id("model", provider_model_id)
    if model_id is None or provider_model_id is None:
        return
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO model_directory (workspace_id, model_id, provider_model_id) VALUES "
                "(:workspace_id, :model_id, :provider_model_id) ON CONFLICT (workspace_id, model_id) "
                "DO UPDATE SET provider_model_id = EXCLUDED.provider_model_id, updated_at = "
                "clock_timestamp()"
            ),
            {
                "workspace_id": workspace_id(),
                "model_id": model_id,
                "provider_model_id": provider_model_id,
            },
        )


@fail_soft
async def upsert_repository(*, full_name: str, private: bool | None) -> None:
    repository_id = opaque_id("repository", full_name.lower())
    if repository_id is None or private is None:
        return
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO repository_directory (workspace_id, repository_id, full_name, private) "
                "VALUES (:workspace_id, :repository_id, :full_name, :private) ON CONFLICT "
                "(workspace_id, repository_id) DO UPDATE SET full_name = EXCLUDED.full_name, "
                "private = EXCLUDED.private, updated_at = clock_timestamp()"
            ),
            {
                "workspace_id": workspace_id(),
                "repository_id": repository_id,
                "full_name": full_name,
                "private": private,
            },
        )


async def resolve_person(
    *,
    immutable_person_key: str | int | None = None,
    github_login: str | None = None,
    email: str | None = None,
    display_name: str | None = None,
    team_key: str | None = None,
) -> UUID | None:
    """Resolve verified product identity, upgrading earlier login/email-only captures."""
    if not configured():
        return None
    login = github_login.strip().lower() if github_login else None
    email = email.strip().lower() if email else None
    immutable_id = opaque_person("github", immutable_person_key)
    login_id = opaque_person("github-login", login)
    email_id = opaque_person("email", email)
    aliases = [value for value in (immutable_id, login_id, email_id) if value is not None]
    if not aliases:
        return None
    async with transaction() as conn:
        # Upgrades can touch several aliases; serialize directory merges per deployment.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
            {"subject": f"analytics-directory:{workspace_id()}"},
        )
        rows = (
            (
                await conn.execute(
                    text("""
                    SELECT d.* FROM identity_directory d
                    WHERE d.workspace_id = :workspace_id AND (
                        d.person_id = :immutable_id
                        OR lower(d.github_login) = :login OR lower(d.email) = :email)
                    ORDER BY d.updated_at DESC, d.person_id
                """),
                    {
                        "workspace_id": workspace_id(),
                        "immutable_id": immutable_id,
                        "login": login,
                        "email": email,
                    },
                )
            )
            .mappings()
            .all()
        )
        current = sorted(
            rows,
            key=lambda row: (
                row["identity_kind"] != "immutable",
                row["github_login"] != login if login else True,
                row["email"] != email if email else True,
            ),
        )
        # Reused handles need a fresh identity; historical aliases must keep their old owner.
        person_id = immutable_id or (current[0]["person_id"] if current else uuid4())
        identity_kind = (
            "immutable"
            if immutable_id
            or any(
                row["person_id"] == person_id and row["identity_kind"] == "immutable"
                for row in rows
            )
            else "provisional"
        )
        merged = [
            row
            for row in rows
            if row["person_id"] == person_id or row["identity_kind"] == "provisional"
        ]
        old_ids = [row["person_id"] for row in merged if row["person_id"] != person_id]
        team_id = opaque_id("team", team_key)
        for row in sorted(merged, key=lambda row: row["person_id"] != person_id):
            login = login or row["github_login"]
            email = email or row["email"]
            display_name = display_name or row["display_name"]
            team_id = team_id or row["team_id"]
        # Mutable handles can change owners without transferring the previous owner's history.
        await conn.execute(
            text("""
                UPDATE identity_directory SET
                    github_login = CASE WHEN lower(github_login) = :login THEN NULL ELSE github_login END,
                    email = CASE WHEN lower(email) = :email THEN NULL ELSE email END
                WHERE workspace_id = :workspace_id AND person_id <> :person_id
                    AND identity_kind = 'immutable'
                    AND (lower(github_login) = :login OR lower(email) = :email)
            """),
            {
                "workspace_id": workspace_id(),
                "person_id": person_id,
                "login": login,
                "email": email,
            },
        )
        if old_ids:
            await conn.execute(
                text(
                    "UPDATE identity_aliases SET person_id = :person_id "
                    "WHERE workspace_id = :workspace_id AND person_id = ANY(CAST(:old_ids AS uuid[]))"
                ),
                {"workspace_id": workspace_id(), "person_id": person_id, "old_ids": old_ids},
            )
            await conn.execute(
                text(
                    "DELETE FROM identity_directory WHERE workspace_id = :workspace_id "
                    "AND person_id = ANY(CAST(:old_ids AS uuid[]))"
                ),
                {"workspace_id": workspace_id(), "old_ids": old_ids},
            )
        await conn.execute(
            text("""
                INSERT INTO identity_directory (
                    workspace_id, person_id, identity_kind, github_login, display_name, email, team_id, anonymize_after
                ) VALUES (
                    :workspace_id, :person_id, :identity_kind, :login, :display_name, :email, :team_id,
                    clock_timestamp() + (:months * interval '1 month')
                ) ON CONFLICT (workspace_id, person_id) DO UPDATE SET
                    github_login = COALESCE(EXCLUDED.github_login, identity_directory.github_login),
                    display_name = COALESCE(EXCLUDED.display_name, identity_directory.display_name),
                    email = COALESCE(EXCLUDED.email, identity_directory.email),
                    team_id = COALESCE(EXCLUDED.team_id, identity_directory.team_id),
                    anonymize_after = EXCLUDED.anonymize_after, updated_at = clock_timestamp(),
                    identity_kind = EXCLUDED.identity_kind
            """),
            {
                "workspace_id": workspace_id(),
                "person_id": person_id,
                "login": login,
                "identity_kind": identity_kind,
                "display_name": display_name,
                "email": email,
                "team_id": team_id,
                "months": ENV.ANALYTICS_PERSON_MONTHS.get_int(13),
            },
        )
        # Alias IDs can already identify historical runs, even after a handle changes owners.
        for alias in {*aliases, *old_ids, person_id}:
            await conn.execute(
                text(
                    "INSERT INTO identity_aliases (workspace_id, alias_person_id, person_id) "
                    "VALUES (:workspace_id, :alias, :person_id) ON CONFLICT (workspace_id, alias_person_id) "
                    "DO NOTHING"
                ),
                {"workspace_id": workspace_id(), "alias": alias, "person_id": person_id},
            )
        return person_id
