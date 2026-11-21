"""How commands in the thread's sandbox get GitHub's credentials through its proxy.

The sandbox's proxy config holds no credential (``runtime_api.catalog.SandboxSpec``):
LangSmith asks the backend's proxy callback for the headers of a request to a GitHub host
as it goes out, and the backend answers with a token for the sandbox's thread
(``GITHUB_PROXY_RULES`` say how each host takes it).
"""

import base64
from fnmatch import fnmatchcase
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

# The user git sends with a GitHub token over HTTPS.
GIT_TOKEN_USERNAME: Final = "x-access-token"


class GitHubProxyRule(BaseModel):
    """One of the sandbox's GitHub rules: the hosts it matches and how it presents the token."""

    model_config = ConfigDict(frozen=True)

    name: str
    hosts: tuple[str, ...]
    scheme: Literal["bearer", "basic"]
    env_vars: dict[str, str] = {}

    def matches(self, host: str) -> bool:
        """Whether a request to ``host`` takes this rule's header."""
        return any(fnmatchcase(host.lower(), pattern) for pattern in self.hosts)

    def authorization(self, token: str) -> str:
        if self.scheme == "bearer":
            return f"Bearer {token}"
        return "Basic " + base64.b64encode(f"{GIT_TOKEN_USERNAME}:{token}".encode()).decode()


GITHUB_PROXY_RULES: Final = (
    # `gh` refuses to run without a token in its environment, though the proxy injects
    # the real one on the wire.
    GitHubProxyRule(
        name="github-api",
        hosts=("api.github.com",),
        scheme="bearer",
        env_vars={"GH_TOKEN": "proxy-injected"},
    ),
    GitHubProxyRule(name="github", hosts=("github.com", "*.github.com"), scheme="basic"),
)
