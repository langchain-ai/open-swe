"""The thread's sandbox's proxy config, which gives commands in the sandbox their credentials.

The proxy config is written whole, so one side writes all of it. The backend does,
with a GitHub token it mints for the thread, unless the run's catalog names the
connection the sandbox's GitHub rules carry (``SandboxSpec.github_proxy``). Then MDA
writes it before each command: GitHub's rules from that connection, and the rest
the backend keeps on the sandbox (``SANDBOX_PROXY``), such as its tool channel's
capability, which names the sandbox.
"""

import base64
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


class SandboxProxy(BaseModel):
    """The backend's part of the sandbox's proxy config, which MDA writes beside GitHub's rules."""

    # Its rules carry a capability for the sandbox, so it is never logged.
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)


# Only reads the thread and signs the same capability again, so it is safe to send again.
SANDBOX_PROXY = Endpoint(
    "sandbox_proxy",
    SandboxProxyRequest,
    SandboxProxy,
    seconds=LOOP_CALL_SECONDS,
    delivery="idempotent",
)
