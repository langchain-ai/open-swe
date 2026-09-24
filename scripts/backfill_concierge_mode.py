"""One-time backfill: copy ``dm_session_enabled`` from Store profiles into ``users.preferences``.

Concierge mode used to be the ``dm_session_enabled`` flag on each person's
``["profiles"]`` Store record; it now lives in ``users.preferences``. Run this once
per deployment after the ``user preferences`` migration.

Usage:
    uv run python scripts/backfill_concierge_mode.py --dry-run
    uv run python scripts/backfill_concierge_mode.py

Resolves the deployment URL from ``--url`` or ``LANGGRAPH_URL``, the API key from
``LANGGRAPH_API_KEY`` / ``LANGSMITH_API_KEY``, and the database from ``POSTGRES_URI``.
"""

import argparse
import asyncio
import os

from langgraph_sdk import get_client

from agent.config import ENV
from agent.database import postgres
from agent.users import User, UserPreferencesPatch

_PAGE_SIZE = 100


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


async def _enabled_logins(url: str, api_key: str | None) -> list[str]:
    client = get_client(url=url, api_key=api_key)
    logins: list[str] = []
    offset = 0
    while True:
        page = await client.store.search_items(
            ["profiles"], filter={"dm_session_enabled": True}, limit=_PAGE_SIZE, offset=offset
        )
        items = page["items"]
        logins.extend(item["key"] for item in items)
        if len(items) < _PAGE_SIZE:
            return logins
        offset += _PAGE_SIZE


async def _run(url: str, api_key: str | None, dry_run: bool) -> None:
    postgres.require_configured()
    logins = await _enabled_logins(url, api_key)
    missing: list[str] = []
    for login in logins:
        if dry_run:
            if await User.for_login("github", login) is None:
                missing.append(login)
            continue
        if await User.update_preferences(login, UserPreferencesPatch(concierge_mode=True)) is None:
            missing.append(login)
    verb = "would enable" if dry_run else "enabled"
    print(
        f"{len(logins)} profile(s) had dm_session_enabled; {verb} concierge mode for "
        f"{len(logins) - len(missing)}"
    )
    for login in missing:
        print(f"  no users row for {login}; skipped")


def main() -> None:
    _load_dotenv_if_available()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="LangGraph deployment URL (default: LANGGRAPH_URL)")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()
    url = args.url or ENV.LANGGRAPH_URL.optional()
    if not url:
        raise SystemExit("Set --url or LANGGRAPH_URL")
    api_key = os.environ.get("LANGGRAPH_API_KEY") or ENV.LANGSMITH_API_KEY.optional()
    asyncio.run(_run(url, api_key, args.dry_run))


if __name__ == "__main__":
    main()
