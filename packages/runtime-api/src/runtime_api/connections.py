"""The MDA connections a run reads its credentials from.

A connection is a credential the deployment's LangSmith workspace keeps in Agent
Auth (``mda connections create``). The backend names which ones a run uses, as
it names everything else about the run; the runtime reads them with
``managed_deepagents.connections``, as the person the run acts for or as the
deployment, never the backend. Each slug is in ``mda/CONNECTIONS.md``.
"""

import re
from typing import Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The GitHub App's installation token. The GitHub channel's ``mda deploy`` keeps it
# in Agent Auth, which renews it; nothing creates it with ``mda connections create``.
GITHUB_APP_TOKEN: Final = "mda/github-token"
# The Linear channel's app token, which its ``mda deploy`` keeps in Agent Auth.
LINEAR_APP_TOKEN: Final = "mda/linear-token"

# Linear's MCP server, which takes the app token as ``Authorization: Bearer``. The
# whole origin, so ``/mcp``, ``/mcp/readonly`` and ``/sse`` alike.
_LINEAR_MCP_ORIGIN: Final = ("https", "mcp.linear.app")

# An MCP server's connection, ``open-swe-mcp-<server>``.
_MCP_SERVER_SLUG: Final = re.compile(r"open-swe-mcp-[a-z0-9]+(?:-[a-z0-9]+)*")


class ConnectionRef(BaseModel):
    """A connection's slug, and whose credential under it the run reads."""

    model_config = ConfigDict(frozen=True)

    # As ``managed_deepagents.connections`` takes it, which refuses any other.
    slug: str = Field(pattern=r"^[a-z0-9]+(?:[-/][a-z0-9]+)*$", max_length=64)
    # ``agent``: the deployment's own, the same for every run. ``user``: the person
    # the run acts for; a run that acts for no one reads none.
    owner: Literal["agent", "user"]

    @model_validator(mode="after")
    def _users_own(self) -> Self:
        # MDA keeps its channels' app tokens under ``mda/``, and only as the deployment's.
        if self.owner == "user" and self.slug.startswith("mda/"):
            raise ValueError(f"{self.slug!r} is MDA's own connection, never a person's")
        return self

    def for_mcp_server(self, url: str) -> bool:
        """Whether an MCP server at ``url`` may send this connection's credential.

        An admin or a person picks the server's URL, so only the connections made for
        MCP servers qualify, never one Open SWE or a channel reads for itself, such as
        the GitHub App's installation token. The one exception is the Linear channel's
        app token, and only to Linear's own MCP server: Linear holds one app token per
        app, so a server with its own Linear credential would revoke the channel's.
        """
        if _MCP_SERVER_SLUG.fullmatch(self.slug):
            return True
        return self.slug == LINEAR_APP_TOKEN and self.owner == "agent" and _on_linear_mcp(url)


def _on_linear_mcp(url: str) -> bool:
    """Whether ``url`` is on Linear's MCP origin: no other host, port or userinfo."""
    if any(ord(char) < 33 for char in url):
        return False
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.netloc) == _LINEAR_MCP_ORIGIN
