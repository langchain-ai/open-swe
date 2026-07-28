#!/usr/bin/env python3
"""Create or list the Linear webhooks that trigger Open SWE.

Linear's webhook UI no longer accepts a user-supplied secret (it generates a
``lin_wh_...`` value), but upstream's docs and env layout assume we choose the
secret. The API path still accepts one, so webhooks are created here with the
``LINEAR_WEBHOOK_SECRET`` already in ``.env``.

Two facts learned the hard way, encoded in this script:

* Only workspace **admins** can manage webhooks — the forge-bot runtime key
  gets ``Invalid role: admin required``. Pass a temporary admin key via the
  ``LINEAR_ADMIN_KEY`` env var and revoke it after use. It is never stored.
* ``allPublicTeams: true`` does **not** cover private Linear teams. Each
  private team (e.g. OPE) needs its own webhook: ``--team KEY``.

Usage:
    LINEAR_ADMIN_KEY=lin_api_... speedbay/create_linear_webhook.py            # list
    LINEAR_ADMIN_KEY=lin_api_... speedbay/create_linear_webhook.py --create             # all public teams
    LINEAR_ADMIN_KEY=lin_api_... speedbay/create_linear_webhook.py --create --team OPE  # one private team
"""

from __future__ import annotations

import os
import pathlib
import sys

import requests
from dotenv import dotenv_values

ENV = dotenv_values(pathlib.Path(__file__).resolve().parent.parent / ".env")
API = "https://api.linear.app/graphql"
URL = "https://openswe.speedbay.com/webhooks/linear"


def _gql(query: str, variables: dict | None = None) -> dict:
    """Run a GraphQL request as the admin key, raising on any error."""
    key = os.environ.get("LINEAR_ADMIN_KEY")
    if not key:
        raise SystemExit("set LINEAR_ADMIN_KEY (a workspace admin's key; revoke after use)")
    resp = requests.post(
        API,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": key, "Content-Type": "application/json"},
        timeout=30,
    )
    body = resp.json()
    if resp.status_code != 200 or "errors" in body:
        raise SystemExit(f"Linear API error {resp.status_code}: {str(body)[:400]}")
    return body["data"]


def list_webhooks() -> None:
    nodes = _gql("{ webhooks { nodes { id label url enabled resourceTypes allPublicTeams team { key } } } }")[
        "webhooks"
    ]["nodes"]
    if not nodes:
        print("no webhooks configured")
    for w in nodes:
        scope = (w.get("team") or {}).get("key") or ("all public teams" if w["allPublicTeams"] else "?")
        print(f"  {w['id'][:8]}  {w['label'] or '(no label)':24} {w['url']}")
        print(f"            enabled={w['enabled']} scope={scope} events={w['resourceTypes']}")


def create(team_key: str | None) -> None:
    inp: dict = {
        "label": "Open SWE" + (f" (team {team_key})" if team_key else ""),
        "url": URL,
        "secret": ENV["LINEAR_WEBHOOK_SECRET"],
        "resourceTypes": ["Comment"],
        "enabled": True,
    }
    if team_key:
        teams = _gql(
            "query($k: String!) { teams(filter: {key: {eq: $k}}) { nodes { id } } }", {"k": team_key}
        )["teams"]["nodes"]
        if not teams:
            raise SystemExit(f"no team with key {team_key}")
        inp["teamId"] = teams[0]["id"]
    else:
        inp["allPublicTeams"] = True
    res = _gql(
        "mutation($i: WebhookCreateInput!) { webhookCreate(input: $i) "
        "{ success webhook { id enabled team { key } } } }",
        {"i": inp},
    )["webhookCreate"]
    print("created:", res["success"], res["webhook"])


if __name__ == "__main__":
    if "--create" in sys.argv:
        team = sys.argv[sys.argv.index("--team") + 1] if "--team" in sys.argv else None
        create(team)
    else:
        list_webhooks()
