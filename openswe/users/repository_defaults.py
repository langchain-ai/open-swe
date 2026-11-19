"""Personal global repository defaults."""

from pydantic import BaseModel


class RepositoryDefaults(BaseModel):
    default_repo: str | None = None

    def repository(self) -> str | None:
        return self.default_repo

    def repo_config(self) -> dict[str, str] | None:
        repository = self.repository()
        if not repository:
            return None
        parts = repository.strip().split("/", 1)
        if len(parts) != 2:
            return None
        owner, name = (part.strip() for part in parts)
        return {"owner": owner, "name": name} if owner and name else None
