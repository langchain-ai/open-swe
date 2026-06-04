"""Per-user skills CRUD against the user's Context Hub repo.

Skills are stored in the same LangSmith Hub agent repo the agent reads at run
time (``-/openswe-skills-<login>`` via ``ContextHubBackend``), so anything saved
here is what the user's agent runs will pick up. The repo is always derived from
the authenticated session login — never from request input — so a user can only
read/write their own skills.

Each skill is a ``<name>/SKILL.md`` file with YAML frontmatter (``name`` +
``description``) and a markdown body, matching the Agent Skills spec the
``SkillsMiddleware`` parses.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import yaml
from fastapi import HTTPException
from langsmith import Client
from langsmith.schemas import Entry, FileEntry
from langsmith.utils import LangSmithNotFoundError
from pydantic import BaseModel, field_validator

from ..utils.skills_hub import user_skills_identifier

SKILL_FILE = "SKILL.md"
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_BODY_LEN = 50_000

# Agent Skills spec: lowercase alphanumeric + single hyphens, no leading/trailing
# or doubled hyphens. Must match the parent directory name.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def validate_skill_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_NAME_LEN or not _NAME_RE.match(name):
        raise ValueError("name must be 1-64 chars, lowercase alphanumeric with single hyphens")
    return name


class SkillPayload(BaseModel):
    name: str
    description: str
    body: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return validate_skill_name(v)

    @field_validator("description")
    @classmethod
    def _valid_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description cannot be empty")
        if len(v) > MAX_DESCRIPTION_LEN:
            raise ValueError(f"description exceeds {MAX_DESCRIPTION_LEN} characters")
        return v

    @field_validator("body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        if len(v) > MAX_BODY_LEN:
            raise ValueError(f"body exceeds {MAX_BODY_LEN} characters")
        return v


def compose_skill_md(payload: SkillPayload) -> str:
    """Render a SKILL.md from a payload (YAML frontmatter + markdown body)."""
    frontmatter = yaml.safe_dump(
        {"name": payload.name, "description": payload.description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    body = payload.body.strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n" if body else f"---\n{frontmatter}\n---\n"


def parse_skill_md(name: str, content: str) -> dict[str, Any]:
    """Parse a stored SKILL.md into ``{name, description, body}``.

    Falls back to the directory ``name`` and empty fields if frontmatter is
    missing or malformed, so a hand-edited repo never breaks the listing.
    """
    description = ""
    body = content
    match = _FRONTMATTER_RE.match(content)
    if match:
        try:
            data = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            description = str(data.get("description") or "").strip()
            name = str(data.get("name") or name).strip() or name
        body = match.group(2).strip()
    return {"name": name, "description": description, "body": body}


def _client() -> Client:
    return Client()


def _pull_files(identifier: str) -> tuple[dict[str, Entry], str | None]:
    """Return ``(files, commit_hash)`` for the repo, empty if it doesn't exist."""
    try:
        ctx = _client().pull_agent(identifier)
    except LangSmithNotFoundError:
        return {}, None
    return dict(ctx.files), ctx.commit_hash


def _push(identifier: str, files: dict[str, Entry | None], parent: str | None) -> None:
    _client().push_agent(identifier, files=files, parent_commit=parent)


def _repo_for(login: str) -> str:
    repo = user_skills_identifier(login)
    if not repo:
        raise HTTPException(400, "skills are disabled (OPENSWE_SKILLS_REPO unset)")
    return repo


def _skill_record(name: str, entry: Entry) -> dict[str, Any] | None:
    if not isinstance(entry, FileEntry):
        return None
    return parse_skill_md(name, entry.content)


async def list_user_skills(login: str) -> list[dict[str, Any]]:
    repo = _repo_for(login)
    files, _ = await asyncio.to_thread(_pull_files, repo)
    skills: list[dict[str, Any]] = []
    for path, entry in files.items():
        if not path.endswith(f"/{SKILL_FILE}"):
            continue
        record = _skill_record(path[: -len(f"/{SKILL_FILE}")], entry)
        if record is not None:
            skills.append(record)
    skills.sort(key=lambda s: s["name"])
    return skills


async def get_user_skill(login: str, name: str) -> dict[str, Any] | None:
    repo = _repo_for(login)
    files, _ = await asyncio.to_thread(_pull_files, repo)
    entry = files.get(f"{name}/{SKILL_FILE}")
    return _skill_record(name, entry) if entry is not None else None


async def create_user_skill(login: str, payload: SkillPayload) -> dict[str, Any]:
    repo = _repo_for(login)
    files, parent = await asyncio.to_thread(_pull_files, repo)
    path = f"{payload.name}/{SKILL_FILE}"
    if path in files:
        raise HTTPException(409, f"skill '{payload.name}' already exists")
    content = compose_skill_md(payload)
    await asyncio.to_thread(_push, repo, {path: FileEntry(type="file", content=content)}, parent)
    return parse_skill_md(payload.name, content)


async def update_user_skill(login: str, name: str, payload: SkillPayload) -> dict[str, Any]:
    if payload.name != name:
        raise HTTPException(400, "renaming via update is not supported; delete and recreate")
    repo = _repo_for(login)
    files, parent = await asyncio.to_thread(_pull_files, repo)
    path = f"{name}/{SKILL_FILE}"
    if path not in files:
        raise HTTPException(404, f"skill '{name}' not found")
    content = compose_skill_md(payload)
    await asyncio.to_thread(_push, repo, {path: FileEntry(type="file", content=content)}, parent)
    return parse_skill_md(name, content)


async def delete_user_skill(login: str, name: str) -> None:
    repo = _repo_for(login)
    files, parent = await asyncio.to_thread(_pull_files, repo)
    path = f"{name}/{SKILL_FILE}"
    if path not in files:
        raise HTTPException(404, f"skill '{name}' not found")
    await asyncio.to_thread(_push, repo, {path: None}, parent)
