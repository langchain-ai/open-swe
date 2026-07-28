#!/usr/bin/env python3
"""Get or set the agent's default model without the dashboard.

`LLM_MODEL_ID` in .env does NOT choose the runtime model — it is read only by
`validate_local_dev_llm_config` as a boot-time credential check. The real
precedence is per-thread override -> user profile -> team default, and the team
default lives in the LangGraph Store under namespace ["team_settings"], key
"default" (agent/dashboard/team_settings.py). Normally only the dashboard API
writes it; this script writes it directly over the Store HTTP API so the model is
configurable before the dashboard exists.

Usage:
    speedbay/set_model.py                       # show current settings
    speedbay/set_model.py <model_id> [effort]   # set agent + subagent default
    speedbay/set_model.py --list                # show selectable model ids

Effort is one of: low, medium, high (default: medium).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:2024"
NAMESPACE = ["team_settings"]
KEY = "default"

MODELS = [
    "anthropic:claude-opus-5",
    "anthropic:claude-sonnet-5",
    "anthropic:claude-fable-5",
    "openai:gpt-5.6-sol",
    "openai:gpt-5.6-terra",
    "openai:gpt-5.6-luna",
    "google_genai:gemini-3.6-flash",
    # Upstream lists kimi-k3-code, which does not exist on Fireworks; kimi-k3 does.
    "fireworks:accounts/fireworks/models/kimi-k3",
    "fireworks:accounts/fireworks/models/deepseek-v4-pro",
    "fireworks:accounts/fireworks/models/glm-5p2",
]


def _request(path: str, body: dict, method: str = "POST") -> dict | None:
    """Send JSON to the local LangGraph server; returns the parsed body or None.

    The Store API splits verbs: search is POST /store/items/search, writes are
    PUT /store/items (langgraph_api/api/store.py).
    """
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{path} failed: {exc.code} {exc.read()[:200]!r}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {BASE} — is speedbay/run-dev.sh running? ({exc})") from exc


def get_settings() -> dict:
    """Return stored team settings, or {} when nothing has been written yet."""
    found = _request("/store/items/search", {"namespace_prefix": NAMESPACE, "limit": 10}) or {}
    for item in found.get("items", []):
        if item.get("key") == KEY:
            return item.get("value") or {}
    return {}


def set_model(model_id: str, effort: str) -> None:
    """Write the agent + subagent default model pair into the Store."""
    current = get_settings()
    current.update(
        {
            "default_agent_model": model_id,
            "default_agent_reasoning_effort": effort,
            "default_agent_subagent_model": model_id,
            "default_agent_subagent_reasoning_effort": effort,
        }
    )
    _request("/store/items", {"namespace": NAMESPACE, "key": KEY, "value": current}, method="PUT")


def _print_models(settings: dict) -> None:
    for field in sorted(settings):
        if "model" in field or "effort" in field:
            print(f"  {field:44} {settings[field]!r}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--list":
        print("\n".join(MODELS))
        return
    if not args:
        settings = get_settings()
        if not settings:
            print("no team settings stored — agent falls back to DEFAULT_MODEL_ID (openai:gpt-5.6-sol)")
            return
        _print_models(settings)
        return

    model_id = args[0]
    effort = args[1] if len(args) > 1 else "medium"
    if model_id not in MODELS:
        print(f"warning: {model_id!r} is not in the known list; setting it anyway", file=sys.stderr)
    set_model(model_id, effort)
    print(f"set default agent model to {model_id} (effort={effort})\nverifying:")
    _print_models(get_settings())


if __name__ == "__main__":
    main()
