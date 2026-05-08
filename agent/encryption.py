"""Encryption utilities for sensitive data like tokens."""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)


class EncryptionKeyMissingError(ValueError):
    """Raised when TOKEN_ENCRYPTION_KEY environment variable is not set."""


def _parse_encryption_keys(raw: str) -> list[bytes]:
    """Split TOKEN_ENCRYPTION_KEY into one or more keys (most-recent-first)."""
    keys: list[bytes] = []
    for part in raw.replace("\n", ",").split(","):
        stripped = part.strip()
        if stripped:
            keys.append(stripped.encode())
    return keys


def _get_encryption_keys() -> list[bytes]:
    """Read the ordered key list from TOKEN_ENCRYPTION_KEY (most-recent-first).

    Accepts a single key or a comma/newline separated list. The first key is
    used for encryption; every key is tried for decryption.

    Raises:
        EncryptionKeyMissingError: If TOKEN_ENCRYPTION_KEY is unset or empty.
    """
    explicit_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not explicit_key:
        raise EncryptionKeyMissingError

    keys = _parse_encryption_keys(explicit_key)
    if not keys:
        raise EncryptionKeyMissingError
    return keys


def _get_fernet() -> MultiFernet:
    """Build a MultiFernet from the configured key list."""
    return MultiFernet([Fernet(k) for k in _get_encryption_keys()])


def encrypt_token(token: str) -> str:
    """Encrypt a token under the newest configured key."""
    if not token:
        return ""

    encrypted = _get_fernet().encrypt(token.encode())
    return encrypted.decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a token, trying each configured key in order."""
    if not encrypted_token:
        return ""

    try:
        decrypted = _get_fernet().decrypt(encrypted_token.encode())
        return decrypted.decode()
    except InvalidToken:
        logger.warning("Failed to decrypt token: invalid token")
        return ""
    except EncryptionKeyMissingError:
        logger.warning("Failed to decrypt token: encryption key not set")
        return ""
