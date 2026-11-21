"""How commands in the thread's sandbox get GitHub's credentials through its proxy.

The sandbox's proxy config holds no credential (``runtime_api.catalog.SandboxSpec``):
LangSmith asks the backend's proxy callback for the headers of a request to a GitHub host
as it goes out, and the backend answers with a token for the sandbox's thread
(``GITHUB_PROXY_RULES`` say how each host takes it).

``SANDBOX_PROXY`` is deprecated: MDA deployments from before the catalog carried the
config ask for it here and write what the backend hands over, which is that same config.
"""

import base64
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest

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


class SandboxProxyRequest(RunRequest):
    # The sandbox MDA opened for the run.
    sandbox_id: str
    # Ignored: the config carries no token to mint anew.
    force_refresh: bool = False


class SandboxProxy(BaseModel):
    """The sandbox's whole proxy config, which an older MDA writes as the backend hands it
    over."""

    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    # Always None: the config carries no token, so MDA never asks again for a fresh one.
    expires_at: datetime | None = None


# Only reads the thread and its sandbox, so it is safe to send again.
SANDBOX_PROXY = Endpoint(
    "sandbox_proxy",
    SandboxProxyRequest,
    SandboxProxy,
    seconds=LOOP_CALL_SECONDS,
    delivery="idempotent",
)
