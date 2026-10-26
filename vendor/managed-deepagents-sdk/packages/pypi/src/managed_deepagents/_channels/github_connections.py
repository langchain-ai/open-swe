"""Agent connections the GitHub channel reserves for the deployment's App."""

from __future__ import annotations

from managed_deepagents._channels.http.provider import app_connection_slug

GITHUB_TOKEN_SLUG = "mda/github-token"  # noqa: S105 - a connection name, not a secret
"""Reserved agent connection: an installation token for the App ``mda deploy`` set up."""


def github_app_connection_slug(deployment_id: str, suffix: str | None = None) -> str:
    """The deployment's GitHub App connection, or an App value stored beside it.

    ``suffix`` names the value (``webhook-secret``, ``bot-id``).
    """
    return app_connection_slug("github", deployment_id, suffix)
