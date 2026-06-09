"""Shared builders for dashboard ("Open in Web") URLs."""

import os
from urllib.parse import quote

_DEFAULT_DASHBOARD_BASE_URL = "https://openswe.vercel.app"


def dashboard_thread_url(thread_id: str) -> str | None:
    """Build the dashboard thread URL for a given thread id."""
    base_url = os.environ.get("DASHBOARD_BASE_URL", _DEFAULT_DASHBOARD_BASE_URL).strip().rstrip("/")
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}"
