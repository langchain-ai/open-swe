"""Read-only provider metadata exposed to authenticated desktop clients."""

import re
from pathlib import Path
from typing import Any

from .options import SUPPORTED_MODELS, models_with_profile_context_windows

_BUNDLED_SKILLS_DIR = Path(__file__).resolve().parents[1] / "bundled_skills"
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_DESCRIPTION_LENGTH = 500
_MAX_SKILLS = 100


def _frontmatter(skill_file: Path) -> dict[str, str]:
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}

    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"}:
            values[key.strip()] = value.strip().strip("'\"")
    return values


def bundled_provider_skills() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    try:
        directories = sorted(path for path in _BUNDLED_SKILLS_DIR.iterdir() if path.is_dir())
    except OSError:
        return skills

    for directory in directories[:_MAX_SKILLS]:
        metadata = _frontmatter(directory / "SKILL.md")
        name = metadata.get("name", "").strip()
        description = metadata.get("description", "").strip()
        if name != directory.name or not _SKILL_NAME.fullmatch(name):
            continue
        skill: dict[str, Any] = {
            "name": name,
            "path": f"/bundled-skills/{name}/SKILL.md",
            "scope": "bundled",
            "enabled": True,
        }
        if description:
            skill["description"] = description[:_MAX_DESCRIPTION_LENGTH]
        skills.append(skill)
    return skills


def provider_capabilities_payload() -> dict[str, Any]:
    skills = bundled_provider_skills()
    return {
        "models": models_with_profile_context_windows(SUPPORTED_MODELS),
        "skills": skills,
        "slash_commands": [
            {
                "name": skill["name"],
                **(
                    {"description": skill["description"]}
                    if isinstance(skill.get("description"), str)
                    else {}
                ),
            }
            for skill in skills
        ],
    }
