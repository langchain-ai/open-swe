"""Robin's authorized skill-management shim over Context Hub files."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

import yaml
from fastapi import HTTPException
from langsmith import Client
from pydantic import BaseModel, Field, JsonValue, field_validator
from runtime_api.skill_hub import SkillHubSettings

from openswe.config import ENV
from openswe.users.models import User

if TYPE_CHECKING:
    from deepagents.backends.context_hub import ContextHubBackend

MAX_SKILL_NAME_CHARS = 64
MAX_SKILL_DESCRIPTION_CHARS = 1024
MAX_SKILL_INSTRUCTIONS_CHARS = 20_000
DEFAULT_SKILLS_PAGE_SIZE = 100
MAX_SKILLS_PAGE_SIZE = 100
MAX_ORGANIZATION_SKILLS = 1000
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HUB_PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
logger = logging.getLogger(__name__)


class SkillUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=MAX_SKILL_DESCRIPTION_CHARS)
    instructions: str = Field(default="", max_length=MAX_SKILL_INSTRUCTIONS_CHARS)

    @field_validator("description")
    @classmethod
    def _non_empty_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description cannot be empty")
        return value


class SkillCreate(SkillUpdate):
    name: str = Field(min_length=1, max_length=MAX_SKILL_NAME_CHARS)

    def content(self) -> str:
        # YAML, not JSON, quoting: JSON escapes emoji as surrogate pairs YAML can't rejoin.
        # Readable text where it reads back unchanged; escapes otherwise, since a literal
        # U+0085 is read back as a line break.
        metadata = {"name": self.name, "description": self.description}
        for readable in (True, False):
            frontmatter = yaml.safe_dump(
                metadata, allow_unicode=readable, sort_keys=False, width=10**9
            )
            if yaml.safe_load(frontmatter) == metadata:
                break
        return f"---\n{frontmatter}---\n\n{self.instructions.strip()}\n"

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not _SKILL_NAME_RE.fullmatch(value):
            raise ValueError("name must use lowercase letters, numbers, and single hyphens")
        return value


class HubSkills:
    def __init__(self, backend: ContextHubBackend, owner: UUID | None) -> None:
        self.backend = backend
        self.owner = owner

    @classmethod
    @asynccontextmanager
    async def open(cls, owner: UUID | None = None) -> AsyncGenerator[HubSkills]:
        from deepagents.backends.context_hub import ContextHubBackend

        try:
            settings = await SkillHubSettings.from_env()
            repo = cls.repo(owner)
        except ValueError as exc:
            raise HTTPException(503, "Skills Hub configuration is missing or invalid") from exc
        client = Client(
            api_url=settings.endpoint,
            api_key=settings.api_key.get_secret_value(),
            workspace_id=str(settings.workspace_id),
            auto_batch_tracing=False,
        )
        try:
            yield cls(ContextHubBackend(repo, client=client), owner)
        finally:
            await asyncio.to_thread(client.close)

    @staticmethod
    def repo(owner: UUID | None = None) -> str:
        """The Context Hub repository holding the organization's skills, or ``owner``'s.

        Robin's deployments share one LangSmith workspace, so each environment gets its own
        repositories unless ``ROBIN_SKILLS_HUB_PREFIX`` says otherwise: editing staging's or
        a preview's skills never changes production's.
        """
        prefix = ENV.ROBIN_SKILLS_HUB_PREFIX.optional()
        if prefix is None:
            environment = (ENV.OPENSWE_ENV.optional() or "local").strip().lower()
            prefix = (
                "robin-skills"
                if environment in {"production", "prod"}
                else f"robin-skills-{environment}"
            )
        if not _HUB_PREFIX_RE.fullmatch(prefix):
            raise ValueError("Invalid Context Hub skill repository prefix")
        return f"-/{prefix}" if owner is None else f"-/{prefix}-user-{owner}"

    async def get(self, name: str) -> dict[str, JsonValue] | None:
        name = SkillCreate(name=name, description="valid").name
        # The file's exact text, which ``save`` hands back as the edit's match: ``aread``
        # normalizes line endings, so a file saved with CRLF would never match.
        [download] = await self.backend.adownload_files([f"/{name}/SKILL.md"])
        if download.error == "file_not_found":
            return None
        if download.error or download.content is None:
            raise HTTPException(503, "Cannot read skills Hub")
        try:
            content = download.content.decode("utf-8")
            parts = re.split(r"(?m)^---[ \t]*$", content.replace("\r\n", "\n"), maxsplit=2)
            if len(parts) != 3 or parts[0]:
                raise ValueError("Missing frontmatter")
            metadata = yaml.safe_load(parts[1])
            if not isinstance(metadata, dict) or metadata.get("name") != name:
                raise ValueError("Skill name must match its folder")
            body = SkillCreate(
                name=name, description=metadata.get("description"), instructions=parts[2].strip()
            )
        except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(409, f"Invalid skill file: {name}/SKILL.md") from exc
        return {**body.model_dump(), "content": content, "encoding": "utf-8"}

    async def listed(self) -> list[dict[str, JsonValue]]:
        listing = await self.backend.als("/")
        if listing.error:
            raise HTTPException(503, "Cannot list skills Hub")
        found = []
        for entry in sorted(listing.entries or [], key=lambda entry: entry["path"]):
            name = entry["path"].strip("/")
            if entry.get("is_dir") and _SKILL_NAME_RE.fullmatch(name):
                try:
                    skill = await self.get(name)
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    # A folder Robin didn't write, as in a repository someone made by hand,
                    # is fixed in LangSmith rather than hiding every other skill.
                    logger.warning("Skipping invalid skill file", extra={"skill": name})
                    continue
                if skill:
                    found.append(skill)
        return found

    async def save(self, body: SkillCreate, *, create: bool) -> dict[str, JsonValue]:
        old = await self.get(body.name)
        if (old is None) != create:
            raise HTTPException(
                409 if create else 404, "Skill already exists" if create else "Skill not found"
            )
        if create and self.owner is None and len(await self.listed()) >= MAX_ORGANIZATION_SKILLS:
            raise HTTPException(409, "Skill limit reached")
        path, content = f"/{body.name}/SKILL.md", body.content()
        result = (
            await self.backend.awrite(path, content)
            if old is None
            else await self.backend.aedit(path, str(old["content"]), content)
        )
        if result.error:
            raise HTTPException(409, "Cannot save skill; reload and retry")
        return {**body.model_dump(), "content": content, "encoding": "utf-8"}

    async def delete(self, name: str) -> None:
        existing = await self.get(name)
        if existing is None:
            raise HTTPException(404, "Skill not found")
        if (await self.backend.adelete(f"/{existing['name']}/SKILL.md")).error:
            raise HTTPException(409, "Cannot delete skill; reload and retry")

    @staticmethod
    async def person(login: str, user: User | None = None) -> UUID:
        user = user or (
            await User.get(UUID(login.removeprefix("user:")))
            if login.startswith("user:")
            else await User.for_login("github", login)
        )
        if user is None:
            raise HTTPException(409, "No Robin user record for this login yet")
        return user.id


async def list_skills(
    login: str,
    *,
    limit: int,
    offset: int,
    user: User | None = None,
) -> dict[str, JsonValue]:
    async with HubSkills.open(await HubSkills.person(login, user)) as skills:
        found = await skills.listed()
    return {
        "items": list[JsonValue](found[offset : offset + limit]),
        "next_offset": offset + limit if len(found) > offset + limit else None,
    }


async def create_skill(
    login: str, body: SkillCreate, *, user: User | None = None
) -> dict[str, JsonValue]:
    async with HubSkills.open(await HubSkills.person(login, user)) as skills:
        return await skills.save(body, create=True)


async def update_skill(
    login: str,
    name: str,
    body: SkillUpdate,
    *,
    user: User | None = None,
) -> dict[str, JsonValue]:
    async with HubSkills.open(await HubSkills.person(login, user)) as skills:
        return await skills.save(SkillCreate(name=name, **body.model_dump()), create=False)


async def delete_skill(login: str, name: str, *, user: User | None = None) -> None:
    async with HubSkills.open(await HubSkills.person(login, user)) as skills:
        await skills.delete(name)
