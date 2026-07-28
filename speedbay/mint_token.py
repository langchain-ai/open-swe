#!/usr/bin/env python3
"""Mint a GitHub App installation token for the sandbox.

Open SWE's agent never holds a real GitHub token: prompts hardcode
``GH_TOKEN=dummy`` and a sandbox-side proxy is expected to swap in the real one.
That proxy is LangSmith-only (``agent/utils/github_proxy.py`` returns early for
any other ``SANDBOX_TYPE``), so with the ``local`` backend every ``git``/``gh``
call fails with 401. This script is the credential source that replaces it.

Prints a bare installation token on stdout. Tokens last an hour; we mint per
call rather than caching, because a mint is one API round-trip and caching
correctly (expiry, concurrent writers) is more code than it saves.

ponytail: no cache, mint per call. Add a file cache if git latency ever matters.
"""

from __future__ import annotations

import pathlib
import sys
import time

import jwt
import requests
from dotenv import dotenv_values

ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"


def mint_installation_token() -> str:
    """Return a fresh GitHub App installation token.

    Raises:
        SystemExit: If App credentials are missing or GitHub rejects the request.
    """
    env = dotenv_values(ENV_PATH)
    app_id = env.get("GITHUB_APP_ID")
    private_key = env.get("GITHUB_APP_PRIVATE_KEY")
    installation_id = env.get("GITHUB_APP_INSTALLATION_ID")
    if not (app_id and private_key and installation_id):
        raise SystemExit(
            "missing GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_APP_INSTALLATION_ID in .env"
        )

    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256"
    )
    resp = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    if resp.status_code != 201:
        raise SystemExit(f"installation token request failed: {resp.status_code} {resp.text[:200]}")
    return str(resp.json()["token"])


if __name__ == "__main__":
    sys.stdout.write(mint_installation_token())
