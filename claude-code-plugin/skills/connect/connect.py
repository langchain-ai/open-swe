#!/usr/bin/env python3
"""Redeem an Open SWE connect claim and install the hooks it returns into Claude Code's settings.

Usage: connect.py <claim_url> <claim>

The response carries a long-lived token, so it goes straight into settings.json and
nothing of it is printed: the agent that runs this only sees the outcome.
"""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HOOKS_SUFFIX = "/dashboard/api/claude-code/hooks"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def redeem(url: str, claim: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK):
        raise SystemExit(f"Refusing to send the claim to {url}: it is not HTTPS")
    request = urllib.request.Request(
        url, data=b"", method="POST", headers={"Authorization": f"Bearer {claim}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", exc.reason)
        except ValueError:
            detail = exc.reason
        raise SystemExit(f"Open SWE refused the claim ({exc.code}): {detail}") from None


def load(path: Path) -> dict[str, object]:
    """The settings to merge into, checked before the claim is spent on a file we cannot write."""
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}") from None
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        raise SystemExit(f"Cannot merge into {path}: it is not a settings object")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(path.parent, os.W_OK) or (path.exists() and not os.access(path, os.W_OK)):
        raise SystemExit(f"Cannot write {path}")
    return settings


def install(
    settings: dict[str, object], hooks: dict[str, list[dict[str, object]]], path: Path
) -> None:
    current = settings.setdefault("hooks", {})
    for event in list(current):
        groups = []
        for group in current[event]:
            kept = [
                hook
                for hook in group.get("hooks", [])
                if not str(hook.get("url", "")).endswith(HOOKS_SUFFIX)
            ]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            current[event] = groups
        else:
            del current[event]
    for event, groups in hooks.items():
        current.setdefault(event, []).extend(groups)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".json")
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(settings, file, indent=2)
            file.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: connect.py <claim_url> <claim>")
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"
    settings = load(path)
    minted = redeem(sys.argv[1], sys.argv[2])
    answered = minted.get("settings")
    hooks = answered.get("hooks") if isinstance(answered, dict) else None
    if not isinstance(hooks, dict):
        raise SystemExit("Open SWE answered without hooks")
    install(settings, hooks, path)
    print(f"Connected {minted.get('name')} to Open SWE.")


if __name__ == "__main__":
    main()
