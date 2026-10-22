"""Personal repository defaults, scoped by workspace."""

from pydantic import BaseModel, Field


class RepositoryDefaults(BaseModel):
    default_repo: str | None = None
    workspace_default_repos: dict[str, str] = Field(default_factory=dict)

    def repository(
        self, workspace: str | None = None, *, fallback_global: bool = True
    ) -> str | None:
        scoped = self.workspace_default_repos.get(workspace) if workspace else None
        return scoped or (self.default_repo if fallback_global else None)

    def repo_config(
        self, workspace: str | None = None, *, fallback_global: bool = True
    ) -> dict[str, str] | None:
        repository = self.repository(workspace, fallback_global=fallback_global)
        if not repository:
            return None
        parts = repository.strip().split("/", 1)
        if len(parts) != 2:
            return None
        owner, name = (part.strip() for part in parts)
        return {"owner": owner, "name": name} if owner and name else None
