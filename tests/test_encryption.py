from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from agent.encryption import (
    EncryptionKeyMissingError,
    _get_encryption_key,
    decrypt_token,
    encrypt_token,
)

# A valid Fernet key for testing (url-safe base64, 32 bytes).
_TEST_KEY = Fernet.generate_key().decode()


# -- _get_encryption_key -------------------------------------------------


def test_get_encryption_key_returns_key_when_set(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    assert _get_encryption_key() == _TEST_KEY.encode()


def test_get_encryption_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionKeyMissingError):
        _get_encryption_key()


# -- encrypt_token -------------------------------------------------------


def test_encrypt_token_empty_returns_empty(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    assert encrypt_token("") == ""


def test_encrypt_token_returns_nonempty_ciphertext(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    ciphertext = encrypt_token("ghp_secret123")
    assert ciphertext != ""
    assert ciphertext != "ghp_secret123"


def test_encrypt_token_raises_without_key(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(EncryptionKeyMissingError):
        encrypt_token("ghp_secret123")


# -- decrypt_token -------------------------------------------------------


def test_decrypt_token_empty_returns_empty(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    assert decrypt_token("") == ""


def test_decrypt_token_invalid_token_returns_empty(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    assert decrypt_token("not-a-valid-fernet-token") == ""


def test_decrypt_token_returns_empty_when_key_missing(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    assert decrypt_token("some-encrypted-value") == ""


# -- round-trip -----------------------------------------------------------


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    plaintext = "ghp_ABCDEFghijklmnop1234567890"
    ciphertext = encrypt_token(plaintext)
    assert decrypt_token(ciphertext) == plaintext


def test_decrypt_with_wrong_key_returns_empty(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _TEST_KEY)
    ciphertext = encrypt_token("my-secret-token")

    # Switch to a different key
    different_key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", different_key)
    assert decrypt_token(ciphertext) == ""
