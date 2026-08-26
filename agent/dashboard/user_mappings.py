"""Store-backed bidirectional GitHub ⇄ work-email ⇄ Slack-id user mapping.

Replaces the static ``GITHUB_USER_EMAIL_MAP`` dict. The canonical record is
keyed by GitHub login in the ``["user_mappings"]`` LangGraph Store namespace::

    {
        "github_login": "octocat",
        "work_email": "octo@example.com",
        "slack_user_id": "U123" | None,
        "source": "slack_oauth",
        "status": "active" | "pending",
        "created_at": "...", "updated_at": "...",
    }

Lookups happen on hot paths, some of which are synchronous (commit-author
resolution, comment trust-gating). To serve those without an event loop we
keep an in-process cache of ``{login, email, slack_user_id} -> record`` that
async readers refresh from the Store. The cache is best-effort: a cold cache
falls back to an async Store read where the call site allows it, and sync
call sites degrade to "unmapped" (the same conservative behavior as a missing
dict entry).
"""

import logging
import threading
from typing import Any, Literal

from agent.store import delete_value, get_value, now_iso, put_value, search_values

logger = logging.getLogger(__name__)

USER_MAPPINGS_NAMESPACE: list[str] = ["user_mappings"]

MappingSource = Literal["slack_oauth"]
MappingStatus = Literal["active", "pending"]


def _norm_login(login: str | None) -> str:
    return login.strip() if isinstance(login, str) else ""


def _norm_email(email: str | None) -> str:
    return email.strip().lower() if isinstance(email, str) else ""


def _norm_slack_id(slack_user_id: str | None) -> str:
    return slack_user_id.strip() if isinstance(slack_user_id, str) else ""


# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

_cache_lock = threading.RLock()
_by_login: dict[str, dict[str, Any]] = {}
_by_email: dict[str, dict[str, Any]] = {}
_by_slack_id: dict[str, dict[str, Any]] = {}
_cache_loaded = False


def _index_record(record: dict[str, Any]) -> None:
    login = _norm_login(record.get("github_login"))
    if not login:
        return
    with _cache_lock:
        _by_login[login.lower()] = record
        email = _norm_email(record.get("work_email"))
        if email:
            _by_email[email] = record
        slack_id = _norm_slack_id(record.get("slack_user_id"))
        if slack_id:
            _by_slack_id[slack_id] = record


def _deindex_login(login: str) -> None:
    with _cache_lock:
        existing = _by_login.pop(login.lower(), None)
        if not existing:
            return
        email = _norm_email(existing.get("work_email"))
        if email and _by_email.get(email) is existing:
            _by_email.pop(email, None)
        slack_id = _norm_slack_id(existing.get("slack_user_id"))
        if slack_id and _by_slack_id.get(slack_id) is existing:
            _by_slack_id.pop(slack_id, None)


def prime_cache(records: list[dict[str, Any]]) -> None:
    """Replace the in-process cache with ``records`` (used after a Store load)."""
    global _cache_loaded
    with _cache_lock:
        _by_login.clear()
        _by_email.clear()
        _by_slack_id.clear()
    for record in records:
        if isinstance(record, dict):
            _index_record(record)
    with _cache_lock:
        _cache_loaded = True


def clear_cache() -> None:
    """Drop the in-process cache (forces a reload on next refresh). Test aid."""
    global _cache_loaded
    with _cache_lock:
        _by_login.clear()
        _by_email.clear()
        _by_slack_id.clear()
        _cache_loaded = False


# ---------------------------------------------------------------------------
# Sync cache readers (hot paths without an event loop)
# ---------------------------------------------------------------------------


def cached_email_for_login(login: str | None) -> str | None:
    norm = _norm_login(login)
    if not norm:
        return None
    with _cache_lock:
        record = _by_login.get(norm.lower())
    email = _norm_email(record.get("work_email")) if record else ""
    return email or None


def cached_slack_id_for_login(login: str | None) -> str | None:
    norm = _norm_login(login)
    if not norm:
        return None
    with _cache_lock:
        record = _by_login.get(norm.lower())
    if not record or record.get("status", "active") != "active":
        return None
    slack_id = _norm_slack_id(record.get("slack_user_id"))
    return slack_id or None


def cached_login_for_email(email: str | None) -> str | None:
    norm = _norm_email(email)
    if not norm:
        return None
    with _cache_lock:
        record = _by_email.get(norm)
    return _norm_login(record.get("github_login")) or None if record else None


def cached_login_for_slack_id(slack_user_id: str | None) -> str | None:
    norm = _norm_slack_id(slack_user_id)
    if not norm:
        return None
    with _cache_lock:
        record = _by_slack_id.get(norm)
    return _norm_login(record.get("github_login")) or None if record else None


def is_login_mapped(login: str | None) -> bool:
    """Whether ``login`` has an active mapping in the cache (trust-gate use)."""
    norm = _norm_login(login)
    if not norm:
        return False
    with _cache_lock:
        record = _by_login.get(norm.lower())
    return bool(record) and record.get("status", "active") == "active"


# ---------------------------------------------------------------------------
# Store access
# ---------------------------------------------------------------------------


async def refresh_cache() -> list[dict[str, Any]]:
    """Load every mapping from the Store and replace the in-process cache."""
    records = await search_values(USER_MAPPINGS_NAMESPACE, limit=1000)
    prime_cache(records)
    return records


async def _ensure_cache_loaded() -> None:
    """Prime the cache if it is cold.

    Fail-soft on purpose: the async lookups below run on webhook and commit
    paths where an unresolvable user is an expected outcome, so a store failure
    degrades to "unmapped" instead of breaking the caller.
    """
    with _cache_lock:
        loaded = _cache_loaded
    if not loaded:
        try:
            await refresh_cache()
        except Exception:
            logger.warning("user mapping cache load failed", exc_info=True)


async def get_mapping(login: str) -> dict[str, Any] | None:
    norm = _norm_login(login)
    if not norm:
        return None
    return await get_value(USER_MAPPINGS_NAMESPACE, norm.lower())


async def list_mappings() -> list[dict[str, Any]]:
    records = await refresh_cache()
    return sorted(records, key=lambda r: _norm_login(r.get("github_login")).lower())


async def email_for_login(login: str | None) -> str | None:
    """Async login→email with cache fallthrough to the Store."""
    cached = cached_email_for_login(login)
    if cached is not None:
        return cached
    await _ensure_cache_loaded()
    return cached_email_for_login(login)


async def slack_id_for_login(login: str | None) -> str | None:
    """Async login→Slack ID with cache fallthrough to the Store."""
    cached = cached_slack_id_for_login(login)
    if cached is not None:
        return cached
    await _ensure_cache_loaded()
    return cached_slack_id_for_login(login)


async def login_for_email(email: str | None) -> str | None:
    """Async email→login with cache fallthrough to the Store."""
    cached = cached_login_for_email(email)
    if cached is not None:
        return cached
    await _ensure_cache_loaded()
    return cached_login_for_email(email)


async def login_for_slack_id(slack_user_id: str | None) -> str | None:
    cached = cached_login_for_slack_id(slack_user_id)
    if cached is not None:
        return cached
    await _ensure_cache_loaded()
    return cached_login_for_slack_id(slack_user_id)


async def upsert_mapping(
    *,
    github_login: str,
    work_email: str,
    slack_user_id: str | None = None,
    source: MappingSource = "slack_oauth",
    status: MappingStatus = "active",
) -> dict[str, Any]:
    """Create or update a mapping keyed by GitHub login."""
    login = _norm_login(github_login)
    if not login:
        raise ValueError("github_login is required")
    email = _norm_email(work_email)
    if not email:
        raise ValueError("work_email is required")

    existing = await get_mapping(login) or {}
    record: dict[str, Any] = {
        "github_login": login,
        "work_email": email,
        "slack_user_id": _norm_slack_id(slack_user_id) or existing.get("slack_user_id") or None,
        "source": source,
        "status": status,
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    await put_value(USER_MAPPINGS_NAMESPACE, login.lower(), record)
    _deindex_login(login)
    _index_record(record)
    return record


async def delete_mapping(github_login: str) -> bool:
    login = _norm_login(github_login)
    if not login:
        return False
    existed = await get_mapping(login) is not None
    await delete_value(USER_MAPPINGS_NAMESPACE, login.lower())
    _deindex_login(login)
    return existed
