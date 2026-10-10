"""How a pull request is named to people: ``repo#7`` in the install's own org, else ``owner/repo#7``."""

from openswe.config import ENV


def home_org() -> str | None:
    """The one GitHub org this install serves; ``None`` when it allows several or none."""
    orgs = {org.lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()}
    return orgs.pop() if len(orgs) == 1 else None


def repo_label(owner: str, repo: str) -> str:
    return repo if owner.lower() == home_org() else f"{owner}/{repo}"


def pr_label(owner: str, repo: str, number: int) -> str:
    return f"{repo_label(owner, repo)}#{number}"
