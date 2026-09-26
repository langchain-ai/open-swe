import asyncio

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import LsResult, ReadResult
from deepagents.backends.state import StateBackend

from agent.sandboxes.read_only_backend import PrefixedReadOnlyBackend


class _Backend:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def als(self, path: str) -> LsResult:
        self.paths.append(path)
        return LsResult(entries=[{"path": f"{path}/repo-skill"}])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        del offset, limit
        self.paths.append(file_path)
        return ReadResult(error=f"File '{file_path}' not found")


def test_prefixed_route_restores_absolute_backend_paths() -> None:
    prefix = "/workspace/repo/.agents/skills/"
    backend = _Backend()
    composite = CompositeBackend(
        default=StateBackend(),
        routes={prefix: PrefixedReadOnlyBackend(backend, prefix)},
    )

    result = asyncio.run(composite.als(prefix))
    read_result = asyncio.run(composite.aread(f"{prefix}missing/SKILL.md"))

    assert result.entries == [{"path": f"{prefix}repo-skill"}]
    assert read_result.error == f"File '{prefix}missing/SKILL.md' not found"
    assert backend.paths == [prefix, f"{prefix}missing/SKILL.md"]


def test_prefixed_route_rewrites_virtual_backend_errors() -> None:
    prefix = "/bundled-skills/"
    backend = _Backend()
    composite = CompositeBackend(
        default=StateBackend(),
        routes={prefix: PrefixedReadOnlyBackend(backend, prefix, reattach=False)},
    )

    result = asyncio.run(composite.aread(f"{prefix}missing/SKILL.md"))

    assert result.error == f"File '{prefix}missing/SKILL.md' not found"
    assert backend.paths == ["/missing/SKILL.md"]
