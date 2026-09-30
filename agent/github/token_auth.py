"""Bearer-token detection for dashboard API requests.

The dashboard API is cookie-authenticated through the GitHub OAuth login flow.
A request that carries a bearer token instead is not a browser form post, so it
is exempt from the CSRF origin check.
"""

from fastapi import Request


def bearer_github_token(request: Request) -> str | None:
    """Return the ``Authorization: Bearer`` token, if the request carries one."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    return value.strip() or None
