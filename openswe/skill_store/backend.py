"""The agent's read-only view of skills, kept out of the web app's import graph."""

from collections.abc import Iterable

from deepagents.backends.store import StoreBackend
from langgraph.store.base import Op, Result
from langgraph.store.memory import InMemoryStore

from openswe.database.agent_store import AgentStore
from openswe.sandboxes.read_only_backend import ReadOnlyBackend
from openswe.skill_store.store import agent_skills, skill_path


class _SkillFiles(AgentStore):
    """The organization's skills, or ``login``'s, loaded on the agent's first read."""

    def __init__(self, login: str | None) -> None:
        super().__init__()
        self.login = login
        self._files: InMemoryStore | None = None

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        if self._files is None:
            self._files = InMemoryStore()
            for skill in await agent_skills(self.login):
                await self._files.aput(("skills",), skill_path(skill["name"]), skill)
        return await self._files.abatch(ops)


def skills_backend(login: str | None) -> ReadOnlyBackend:
    """The organization's skills, or ``login``'s, as read-only files for the agent."""
    return ReadOnlyBackend(
        StoreBackend(store=_SkillFiles(login), namespace=lambda _runtime: ("skills",))
    )
