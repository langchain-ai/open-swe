"""PKCE (RFC 7636) helpers, shared by every OAuth client that uses it."""

import base64
import hashlib


def s256_challenge(verifier: str) -> str:
    """The PKCE ``S256`` challenge for a verifier: unpadded base64url SHA-256."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
