import pytest
from cryptography.fernet import Fernet

from openswe.encryption import (
    decrypt_token,
    encrypt_token,
)


def _set_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", value)


class TestSingleKeyRoundtrip:
    def test_invalid_ciphertext_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, Fernet.generate_key().decode())
        assert decrypt_token("not-a-valid-fernet-token") == ""

    def test_decrypt_without_key_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
        assert decrypt_token("anything") == ""


class TestMultiKeyDecrypt:
    def test_decrypt_fails_when_no_key_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        old_key = Fernet.generate_key().decode()
        _set_key(monkeypatch, old_key)
        old_ciphertext = encrypt_token("ghp_token")

        unrelated_key = Fernet.generate_key().decode()
        _set_key(monkeypatch, unrelated_key)
        assert decrypt_token(old_ciphertext) == ""


class TestRotationRoundtrip:
    def test_full_rotation_lifecycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        _set_key(monkeypatch, old_key)
        token = "ghp_lifecycle"
        old_ciphertext = encrypt_token(token)

        _set_key(monkeypatch, f"{new_key},{old_key}")
        assert decrypt_token(old_ciphertext) == token
        re_encrypted = encrypt_token(decrypt_token(old_ciphertext))
        assert re_encrypted != old_ciphertext

        _set_key(monkeypatch, new_key)
        assert decrypt_token(re_encrypted) == token
        assert decrypt_token(old_ciphertext) == ""
