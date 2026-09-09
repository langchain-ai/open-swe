"""Protected analytics identity and repository directories."""

from sqlalchemy import text

from agent.analytics.database import configured, transaction
from agent.analytics.emitter import opaque_id, opaque_person, workspace_id
from agent.config import ENV


async def upsert_person(
    *,
    provider: str,
    immutable_person_key: str | int | None,
    github_login: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    team_key: str | None = None,
) -> None:
    person_id = opaque_person(provider, immutable_person_key)
    if not configured() or person_id is None:
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


async def upsert_model(provider_model_id: str | None) -> None:
    model_id = opaque_id("model", provider_model_id)
    if not configured() or model_id is None or provider_model_id is None:
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


async def upsert_repository(*, full_name: str, private: bool | None) -> None:
    repository_id = opaque_id("repository", full_name.lower())
    if not configured() or repository_id is None or private is None:
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
