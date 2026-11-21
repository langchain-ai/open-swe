"""The starter offers, one Markdown file each under ``resources/prompts/proactivity/playbooks``."""

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from importlib import resources
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt
from openswe.threads.index import IndexedThread

_DIRECTORY = "proactivity/playbooks"
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)

BUSY_DAY_THREADS = 4
STALLED_AFTER = timedelta(days=3)

# What has to hold in the person's threads before the heartbeat may weigh the offer;
# ``threads`` leaves the whole judgment to the heartbeat's model.
type Evidence = Literal["threads", "busy_day", "stalled_threads"]


class Playbook(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    evidence: Evidence
    signal: str
    offer: str
    never: str
    cron: str
    # The prompt every run of the automation it creates starts from.
    run_prompt: str = Field(min_length=1)

    @property
    def automation_name(self) -> str:
        return f"Robin: {self.name}"

    @classmethod
    async def catalog(cls) -> tuple[Self, ...]:
        root = resources.files("openswe.resources").joinpath("prompts", *_DIRECTORY.split("/"))
        names = sorted(
            entry.name.removesuffix(".md") for entry in root.iterdir() if entry.name.endswith(".md")
        )
        return tuple([await cls.load(name) for name in names])

    @classmethod
    async def load(cls, playbook_id: str) -> Self:
        match = _FRONTMATTER.match(prompt(f"{_DIRECTORY}/{playbook_id}") + "\n")
        if match is None:
            raise ValueError(f"playbook {playbook_id!r} has no frontmatter")
        front = yaml.safe_load(match.group(1))
        return cls.model_validate(
            {**front, "id": playbook_id, "run_prompt": match.group(2).strip()}
        )

    @classmethod
    async def named(cls, playbook_id: str) -> Self | None:
        return next((p for p in await cls.catalog() if p.id == playbook_id), None)

    def evidence_in(self, threads: Sequence[IndexedThread], now: datetime) -> str | None:
        """What backs this offer in the person's threads; ``None`` when nothing does."""
        match self.evidence:
            case "threads":
                return ""
            case "busy_day":
                active = sum(1 for t in threads if t.updated_at >= now - timedelta(days=1))
                if active < BUSY_DAY_THREADS:
                    return None
                return f"{active} threads active in the last day"
            case "stalled_threads":
                stalled = sum(
                    1
                    for thread in threads
                    if thread.metadata.get("resolved") is not True
                    and thread.updated_at <= now - STALLED_AFTER
                )
                if not stalled:
                    return None
                return f"{stalled} unresolved threads quiet for {STALLED_AFTER.days}+ days"
