"""Invite-only email/password accounts for dashboard login."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from ..utils.thread_ops import langgraph_client

PASSWORD_ACCOUNTS_NAMESPACE: list[str] = ["dashboard_password_accounts"]
PASSWORD_RESET_NAMESPACE: list[str] = ["dashboard_password_resets"]
PASSWORD_HASH_ALG = "pbkdf2_sha256"
PASSWORD_MIN_LENGTH = 12
RESET_TTL_SECONDS = 60 * 60


def _client():
    return langgraph_client()


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _secret() -> str:
    secret = os.environ.get("DASHBOARD_JWT_SECRET", "")
    if not secret:
        raise HTTPException(500, "DASHBOARD_JWT_SECRET not configured")
    return secret


def _iterations() -> int:
    raw = os.environ.get("DASHBOARD_PASSWORD_PBKDF2_ITERATIONS")
    if raw and raw.isdigit():
        return max(int(raw), 600_000)
    return 600_000


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value or len(value) > 320:
        raise HTTPException(400, "valid email is required")
    return value


def _require_password_strength(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(400, "password is too short")


def _value_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _derive_password_hash(password: str, *, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def hash_password(password: str) -> str:
    _require_password_strength(password)
    salt = secrets.token_urlsafe(24)
    iterations = _iterations()
    digest = _derive_password_hash(password, salt=salt, iterations=iterations)
    return f"{PASSWORD_HASH_ALG}${iterations}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        alg, raw_iterations, salt, expected = password_hash.split("$", 3)
        iterations = int(raw_iterations)
    except ValueError:
        return False
    if alg != PASSWORD_HASH_ALG or iterations < 1:
        return False
    actual = _derive_password_hash(password, salt=salt, iterations=iterations)
    return hmac.compare_digest(actual, expected)


async def get_password_account(email: str) -> dict[str, Any] | None:
    key = normalize_email(email)
    item = await _client().store.get_item(PASSWORD_ACCOUNTS_NAMESPACE, key)
    return _value_from_item(item)


def _redacted_account(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "login": record.get("login"),
        "email": record.get("email"),
        "enabled": bool(record.get("enabled")),
        "auth_source": "password",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "password_set_at": record.get("password_set_at"),
    }


async def upsert_password_account(
    *,
    login: str,
    email: str,
    password: str,
    enabled: bool,
    invited_by: str | None = None,
) -> dict[str, Any]:
    normalized_email = normalize_email(email)
    login = login.strip()
    if not login:
        raise HTTPException(400, "login is required")
    existing = await get_password_account(normalized_email)
    now = _now_iso()
    record = {
        **(existing or {}),
        "login": login,
        "email": normalized_email,
        "password_hash": hash_password(password),
        "enabled": enabled,
        "auth_source": "password",
        "invite_only": True,
        "invited_by": invited_by,
        "password_set_at": now,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    await _client().store.put_item(PASSWORD_ACCOUNTS_NAMESPACE, normalized_email, record)
    return _redacted_account(record)


async def set_password_account_enabled(email: str, *, enabled: bool) -> dict[str, Any]:
    normalized_email = normalize_email(email)
    record = await get_password_account(normalized_email)
    if record is None:
        raise HTTPException(404, "account not found")
    updated = {**record, "enabled": enabled, "updated_at": _now_iso()}
    await _client().store.put_item(PASSWORD_ACCOUNTS_NAMESPACE, normalized_email, updated)
    return _redacted_account(updated)


async def authenticate_password(email: str, password: str) -> dict[str, Any]:
    account = await get_password_account(email)
    if account is None or not verify_password(password, str(account.get("password_hash") or "")):
        raise HTTPException(401, "invalid email or password")
    if account.get("enabled") is not True:
        raise HTTPException(403, "account disabled")
    return _redacted_account(account)


def _token_digest(token: str) -> str:
    return hmac.new(_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


def _expires_at(ttl_seconds: int = RESET_TTL_SECONDS) -> str:
    return (_now() + timedelta(seconds=ttl_seconds)).isoformat()


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    try:
        return datetime.fromisoformat(expires_at) <= _now()
    except ValueError:
        return True


async def create_password_reset_token(
    email: str,
    *,
    requested_by: str,
) -> dict[str, str]:
    account = await get_password_account(email)
    if account is None:
        raise HTTPException(404, "account not found")
    token = secrets.token_urlsafe(48)
    digest = _token_digest(token)
    record = {
        "token_digest": digest,
        "email": account["email"],
        "requested_by": requested_by,
        "created_at": _now_iso(),
        "expires_at": _expires_at(),
        "used_at": None,
    }
    await _client().store.put_item(PASSWORD_RESET_NAMESPACE, digest, record)
    return {"token": token, "email": str(account["email"]), "expires_at": record["expires_at"]}


async def request_password_reset(email: str) -> None:
    account = await get_password_account(email)
    if account is None or account.get("enabled") is not True:
        return
    await create_password_reset_token(str(account["email"]), requested_by="self-service")


async def reset_password(token: str, password: str) -> dict[str, Any]:
    digest = _token_digest(token.strip())
    item = await _client().store.get_item(PASSWORD_RESET_NAMESPACE, digest)
    record = _value_from_item(item)
    if record is None or record.get("used_at") or _is_expired(record.get("expires_at")):
        raise HTTPException(400, "invalid or expired reset token")

    email = normalize_email(str(record.get("email") or ""))
    account = await get_password_account(email)
    if account is None:
        raise HTTPException(400, "invalid or expired reset token")
    now = _now_iso()
    updated_account = {
        **account,
        "password_hash": hash_password(password),
        "password_set_at": now,
        "updated_at": now,
    }
    used_reset = {**record, "used_at": now}
    await _client().store.put_item(PASSWORD_ACCOUNTS_NAMESPACE, email, updated_account)
    await _client().store.put_item(PASSWORD_RESET_NAMESPACE, digest, used_reset)
    return _redacted_account(updated_account)
