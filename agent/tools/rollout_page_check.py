"""Page check for a rollout. The bot login stays on the server.

The sandbox has no browser runtime, so this tool does not log in and does not
fetch the page. It reports whether ``ROLLOUT_BOT_EMAIL`` and
``ROLLOUT_BOT_PASSWORD`` are set. The sign-in form takes that email. Values
are never returned or logged.
"""

import os
from typing import Any
from urllib.parse import urlparse

_EMAIL = "ROLLOUT_BOT_EMAIL"
_PASSWORD = "ROLLOUT_BOT_PASSWORD"


def _configured(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


async def rollout_page_check(url: str, expected: str = "") -> dict[str, Any]:
    """Implement the `rollout_page_check` tool."""
    del expected
    parsed = urlparse(url.strip())
    if parsed.username or parsed.password or parsed.scheme != "https" or not parsed.hostname:
        return {"ok": False, "reason": "invalid_url"}
    missing = [name for name in (_EMAIL, _PASSWORD) if not _configured(name)]
    if missing:
        return {"ok": False, "reason": "credentials_missing", "missing": missing}
    return {
        "ok": False,
        "reason": "browser_unavailable",
        "email_configured": True,
        "password_configured": True,
    }
