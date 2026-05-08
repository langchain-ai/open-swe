from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from agent.encryption import (
    EncryptionKeyMissingError,
    _get_encryption_keys,
    _parse_encryption_keys,
    decrypt_token,
    encrypt_token,
)


def _set_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", value)


class TestParseEncryptionKeys:
    def test_single_key(self) -> None:
        k = Fernet.generate_key().decode()
        assert _parse_encryption_keys(k) == [k.encode()]

    def test_comma_separated(self) -> None:
        k1 = Fernet.generate_key().decode()
        k2 = Fernet.generate_key().decode()
        assert _parse_encryption_keys(f"{k1},{k2}") == [k1.encode(), k2.encode()]

    def test_newline_separated(self) -> None:
        k1 = Fernet.generate_key().decode()
        k2 = Fernet.generate_key().decode()
        assert _parse_encryption_keys(f"{k1}\n{k2}") == [k1.encode(), k2.encode()]

    def test_strips_whitespace_and_empties(self) -> None:
        k1 = Fernet.generate_key().decode()
        k2 = Fernet.generate_key().decode()
        raw = f"  {k1}  ,, \n  {k2}\n,\n"
        assert _parse_encryption_keys(raw) == [k1.encode(), k2.encode()]


class TestGetEncryptionKeys:
    def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
        with pytest.raises(EncryptionKeyMissingError):
            _get_encryption_keys()

    def test_empty_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, "")
        with pytest.raises(EncryptionKeyMissingError):
            _get_encryption_keys()

    def test_whitespace_only_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, "  ,\n  ")
        with pytest.raises(EncryptionKeyMissingError):
            _get_encryption_keys()


class TestSingleKeyRoundtrip:
    def test_encrypt_decrypt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, Fernet.generate_key().decode())
        token = "ghp_abc123"
        ciphertext = encrypt_token(token)
        assert ciphertext != ""
        assert ciphertext != token
        assert decrypt_token(ciphertext) == token

    def test_empty_token_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, Fernet.generate_key().decode())
        assert encrypt_token("") == ""
        assert decrypt_token("") == ""

    def test_invalid_ciphertext_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_key(monkeypatch, Fernet.generate_key().decode())
        assert decrypt_token("not-a-valid-fernet-token") == ""

    def test_decrypt_without_key_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
        assert decrypt_token("anything") == ""


class TestMultiKeyDecrypt:
    def test_decrypt_old_ciphertext_after_prepending_new_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        _set_key(monkeypatch, old_key)
        token = "ghp_old_secret"
        old_ciphertext = encrypt_token(token)

        _set_key(monkeypatch, f"{new_key},{old_key}")
        assert decrypt_token(old_ciphertext) == token

    def test_encrypts_under_first_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        new_key = Fernet.generate_key().decode()
        old_key = Fernet.generate_key().decode()

        _set_key(monkeypatch, f"{new_key},{old_key}")
        token = "ghp_new_secret"
        ciphertext = encrypt_token(token)

        assert Fernet(new_key.encode()).decrypt(ciphertext.encode()).decode() == token

    def test_decrypt_fails_when_no_key_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        old_key = Fernet.generate_key().decode()
        _set_key(monkeypatch, old_key)
        old_ciphertext = encrypt_token("ghp_token")

        unrelated_key = Fernet.generate_key().decode()
        _set_key(monkeypatch, unrelated_key)
        assert decrypt_token(old_ciphertext) == ""

    def test_newline_separated_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        _set_key(monkeypatch, old_key)
        old_ciphertext = encrypt_token("ghp_old")

        _set_key(monkeypatch, f"{new_key}\n{old_key}")
        assert decrypt_token(old_ciphertext) == "ghp_old"


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
