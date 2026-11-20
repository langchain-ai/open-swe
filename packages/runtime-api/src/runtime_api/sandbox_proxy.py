"""The thread's sandbox's proxy config, which gives commands in the sandbox their credentials.

The proxy config is written whole, so one side writes all of it: MDA, on every sandbox
but an external one, before each command, as the backend hands it over
(``SANDBOX_PROXY``): GitHub's rules, with a token the backend minted for the run's scope,
and the rest the backend keeps on the sandbox, such as its tool channel's capability,
which names the sandbox. MDA asks for it again before the token expires.
"""

import base64
from datetime import datetime
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
    # The sandbox MDA opened for the run, which the capability in the answer names.
    sandbox_id: str
    # GitHub refused the token the last answer carried: mint another, not a cached one.
    force_refresh: bool = False


class SandboxProxy(BaseModel):
    """The sandbox's whole proxy config, which MDA writes as the backend hands it over."""

    # Its rules carry a GitHub token and a capability for the sandbox, so it is never logged.
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    # When the GitHub token in ``config`` expires, so MDA asks again before then; None
    # where it carries none.
    expires_at: datetime | None = None


# Only reads the thread and signs the same capability again, so it is safe to send again.
SANDBOX_PROXY = Endpoint(
    "sandbox_proxy",
    SandboxProxyRequest,
    SandboxProxy,
    seconds=LOOP_CALL_SECONDS,
    delivery="idempotent",
)
