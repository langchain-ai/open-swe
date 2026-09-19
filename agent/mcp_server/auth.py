"""Bearer-token authentication for the MCP endpoint.

Tokens are HMAC-signed, expiring, and bound to a user's work email. An operator
mints one per user (``python -m agent.mcp_server.auth mint alice@corp.com``),
and the user pastes it into their MCP client's ``Authorization`` header.

``resolve_caller`` is the single seam where a token's email becomes the identity
a run is attributed to. Point it at the same resolver the Slack integration uses
(``agent/users/resolve.py``) so both surfaces share one user mapping.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

TOKEN_PREFIX = "oswe_mcp_"
DEFAULT_TTL_SECONDS = 30 * 24 * 3600


class AuthError(Exception):
    """The bearer token is missing, malformed, expired, or not recognised."""


@dataclass(frozen=True)
class Caller:
    """The authenticated identity a review run is attributed to."""

    user_id: str
    email: str


def _secret() -> bytes:
    secret = os.environ.get("MCP_TOKEN_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("MCP_TOKEN_SECRET must be set to at least 32 characters")
    return secret.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def mint_token(email: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    payload = {"v": 1, "sub": email.strip().lower(), "exp": int(time.time()) + ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{TOKEN_PREFIX}{body}.{_sign(body)}"


def resolve_caller(email: str) -> Caller:
    """Map a token's email to a Caller.

    TODO: replace with the shared resolver (agent/users/resolve.py) so that
    unknown or unmapped users are rejected exactly as they are for Slack.
    Raise ``AuthError`` for anyone who should not be allowed to dispatch runs.
    """
    return Caller(user_id=email, email=email)


def verify_token(token: str) -> Caller:
    if not token.startswith(TOKEN_PREFIX):
        raise AuthError("unrecognised token")
    body, _, signature = token[len(TOKEN_PREFIX) :].partition(".")
    if not body or not signature:
        raise AuthError("malformed token")
    if not hmac.compare_digest(signature, _sign(body)):
        raise AuthError("bad signature")
    try:
        payload = json.loads(_unb64(body))
        email = str(payload["sub"])
        expires_at = int(payload["exp"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("malformed token") from exc
    if expires_at < time.time():
        raise AuthError("token expired")
    return resolve_caller(email)


def _main() -> None:
    parser = argparse.ArgumentParser(prog="python -m agent.mcp_server.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    mint = sub.add_parser("mint", help="mint a bearer token for a user")
    mint.add_argument("email")
    mint.add_argument("--days", type=int, default=DEFAULT_TTL_SECONDS // 86400)
    args = parser.parse_args()
    print(mint_token(args.email, ttl_seconds=args.days * 86400))


if __name__ == "__main__":
    _main()
