"""Backend helpers for skill route diagnostics."""

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import ReadResult


class SkillCompositeBackend(CompositeBackend):
    """Add route context when a mounted skill read misses."""

    def _with_route_diagnostic(self, file_path: str, result: ReadResult) -> ReadResult:
        if (
            result.error
            and file_path.startswith(tuple(self.routes))
            and "not found" in result.error.lower()
        ):
            routes = ", ".join(self.routes) or "(none)"
            result.error = (
                f"{result.error}; requested path '{file_path}'. Mounted skill routes: {routes}"
            )
        return result

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._with_route_diagnostic(
            file_path, super().read(file_path, offset=offset, limit=limit)
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._with_route_diagnostic(
            file_path, await super().aread(file_path, offset=offset, limit=limit)
        )
