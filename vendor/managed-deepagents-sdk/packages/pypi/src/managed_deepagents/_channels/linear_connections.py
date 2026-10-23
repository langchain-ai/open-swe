"""Agent connections the Linear channel reserves for the deployment's app."""

from __future__ import annotations

from managed_deepagents._channels.http.provider import app_connection_slug

LINEAR_TOKEN_SLUG = "mda/linear-token"  # noqa: S105 - a connection name, not a secret
"""Reserved agent connection: the access token of the app ``mda deploy`` set up."""


def linear_app_connection_slug(deployment_id: str, suffix: str | None = None) -> str:
    """The deployment's Linear app connection, or an app value stored beside it.

    ``suffix`` names the value (``webhook-secret``).
    """
    return app_connection_slug("linear", deployment_id, suffix)
