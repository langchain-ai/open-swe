"""Create the GitHub App for an Open SWE deployment and write its credentials.

GitHub's App Manifest flow does the clicking: this script opens your browser on
GitHub with the App preconfigured (permissions, events, webhook URL, login
callback), GitHub creates it and sends its credentials back to a local callback,
and the script writes them where the deployment reads them: a ``.env`` file, or
the environment of a LangGraph Platform deployment. It then waits for you to
install the App and records the installation id the same way.

    # a LangGraph Platform deployment (LANGSMITH_API_KEY must be set)
    uv run python scripts/create_github_app.py --url https://my-open-swe.us.langgraph.app \\
        --deployment 2abd85fb-d8ff-467a-bdab-d36e85148abf --org my-org

    # local development: writes .env, no webhook (GitHub cannot reach localhost)
    uv run python scripts/create_github_app.py --url http://localhost:2024 --env-file .env

Nothing secret is printed.
"""

import argparse
import html
import json
import re
import secrets
import sys
import threading
import time
import webbrowser
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx
import jwt

from agent.config import ENV

GITHUB_URL = "https://github.com"
GITHUB_API_URL = "https://api.github.com"
APP_HOMEPAGE = "https://github.com/langchain-ai/open-swe"
DEFAULT_CONTROL_PLANE = "https://api.host.langchain.com"

# Mirrors docs/INSTALLATION.md, "Create a GitHub App".
PERMISSIONS: dict[str, str] = {
    "contents": "write",
    "pull_requests": "write",
    "issues": "write",
    "checks": "write",
    "statuses": "read",
    "actions": "read",
    "workflows": "write",
    "metadata": "read",
    "members": "read",
}
EVENTS: tuple[str, ...] = (
    "issue_comment",
    "pull_request_review",
    "pull_request_review_comment",
    "check_run",
    "check_suite",
    "workflow_run",
    "status",
)
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def is_public_url(url: str) -> bool:
    """GitHub only delivers webhooks to hosts reachable from the public Internet."""
    host = urlsplit(url).hostname or ""
    return bool(host) and host not in _LOCAL_HOSTS and not host.endswith(".local")


def build_manifest(
    *, url: str, redirect_url: str, name: str, extra_callbacks: tuple[str, ...] = ()
) -> dict[str, Any]:
    """The App GitHub creates. Without a public URL there is no webhook and no events,
    because GitHub rejects a hook it cannot reach and events without a hook."""
    base = url.rstrip("/")
    callbacks = [f"{base}/dashboard/api/auth/callback", *extra_callbacks]
    manifest: dict[str, Any] = {
        "name": name,
        "url": APP_HOMEPAGE,
        "description": "Open SWE coding agent",
        "public": False,
        "redirect_url": redirect_url,
        "callback_urls": callbacks,
        "request_oauth_on_install": False,
        "setup_on_update": False,
        "default_permissions": dict(PERMISSIONS),
    }
    if is_public_url(base):
        manifest["hook_attributes"] = {"url": f"{base}/webhooks/github", "active": True}
        manifest["default_events"] = list(EVENTS)
    return manifest


def manifest_target(state: str, org: str = "") -> str:
    query = urlencode({"state": state})
    if org.strip():
        return f"{GITHUB_URL}/organizations/{quote(org.strip(), safe='')}/settings/apps/new?{query}"
    return f"{GITHUB_URL}/settings/apps/new?{query}"


def exchange_code(code: str) -> dict[str, Any]:
    """Turn GitHub's temporary code into the App's credentials."""
    response = httpx.post(
        f"{GITHUB_API_URL}/app-manifests/{quote(code, safe='')}/conversions",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected manifest conversion response")
    return data


def credentials_from_conversion(data: Mapping[str, Any]) -> dict[str, str]:
    """Env names the deployment reads. A missing webhook secret (no webhook) is generated."""
    values = {
        "GITHUB_APP_ID": str(data.get("id") or ""),
        "GITHUB_APP_CLIENT_ID": str(data.get("client_id") or ""),
        "GITHUB_APP_CLIENT_SECRET": str(data.get("client_secret") or ""),
        "GITHUB_APP_PRIVATE_KEY": str(data.get("pem") or ""),
        "GITHUB_WEBHOOK_SECRET": str(data.get("webhook_secret") or secrets.token_hex(32)),
    }
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise ValueError(f"GitHub's response lacks {', '.join(missing)}")
    return values


# --- where the values go ---------------------------------------------------------------


def _quote_env(value: str) -> str:
    # JSON string syntax is valid double-quoted dotenv: the PEM's newlines become \n escapes.
    return json.dumps(value)


def write_env_values(path: Path, values: Mapping[str, str]) -> None:
    """Set ``values`` in the env file: replace single-line assignments, append the rest.

    The file is the deployment's credential store, so it is kept owner-only.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in remaining:
            lines[index] = f"{match.group(1)}={_quote_env(remaining.pop(match.group(1)))}"
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Added by scripts/create_github_app.py")
        lines.extend(f"{k}={_quote_env(v)}" for k, v in remaining.items())
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n")


def merge_secrets(current: list[dict[str, str]], values: Mapping[str, str]) -> list[dict[str, str]]:
    """A deployment's secrets list with ``values`` set; PATCH replaces the whole list."""
    merged = {item["name"]: item["value"] for item in current}
    merged.update(values)
    return [{"name": k, "value": v} for k, v in merged.items()]


class DeploymentSink:
    """Writes values into a LangGraph Platform deployment's environment."""

    def __init__(self, control_plane: str, deployment_id: str, api_key: str, tenant_id: str):
        self.base = control_plane.rstrip("/")
        self.deployment_id = deployment_id
        self.headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        if tenant_id:
            self.headers["X-Tenant-Id"] = tenant_id

    def write(self, values: Mapping[str, str]) -> None:
        url = f"{self.base}/v2/deployments/{self.deployment_id}"
        current = httpx.get(url, headers=self.headers, timeout=30)
        current.raise_for_status()
        secrets_list = merge_secrets(current.json().get("secrets") or [], values)
        patched = httpx.patch(url, headers=self.headers, json={"secrets": secrets_list}, timeout=60)
        patched.raise_for_status()

    def describe(self) -> str:
        return f"deployment {self.deployment_id} (a new revision is rolling out)"


class EnvFileSink:
    def __init__(self, path: Path):
        self.path = path

    def write(self, values: Mapping[str, str]) -> None:
        write_env_values(self.path, values)

    def describe(self) -> str:
        return str(self.path)


# --- the browser round trip ------------------------------------------------------------

_PAGE = """<!doctype html><meta charset="utf-8"><title>Open SWE · GitHub App</title>
<body style="font:16px system-ui;max-width:40rem;margin:3rem auto">{body}</body>"""


class _CallbackServer(HTTPServer):
    def __init__(self, address: tuple[str, int], manifest: dict[str, Any], target: str, state: str):
        super().__init__(address, _Handler)
        self.manifest = manifest
        self.target = target
        self.state = state
        self.conversion: dict[str, Any] | None = None
        self.error: str | None = None
        self.exchange = exchange_code


class _Handler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  # quiet
        return

    def _send(self, body: str, status: int = 200) -> None:
        data = _PAGE.format(body=body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == "/":
            manifest = html.escape(json.dumps(self.server.manifest), quote=True)
            self._send(
                f"<h1>Creating the GitHub App</h1><p>Continuing on GitHub…</p>"
                f'<form id="f" method="post" action="{html.escape(self.server.target, quote=True)}">'
                f'<input type="hidden" name="manifest" value="{manifest}">'
                '<button type="submit">Continue to GitHub</button></form>'
                '<script>document.getElementById("f").submit()</script>'
            )
            return
        if parts.path == "/callback":
            query = parse_qs(parts.query)
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            if state != self.server.state or not code:
                self.server.error = "GitHub returned an unexpected state; run the script again."
                self._send(f"<h1>Something went wrong</h1><p>{self.server.error}</p>", 400)
                return
            try:
                self.server.conversion = self.server.exchange(code)
            except Exception as exc:  # noqa: BLE001
                self.server.error = f"could not exchange GitHub's code: {exc}"
                self._send(f"<h1>Something went wrong</h1><p>{html.escape(str(exc))}</p>", 502)
                return
            self._send(
                "<h1>GitHub App created</h1><p>You can close this tab and return to the terminal.</p>"
            )
            return
        self._send("<h1>Not found</h1>", 404)


def run_manifest_flow(
    *,
    url: str,
    name: str,
    org: str,
    port: int,
    open_browser: bool,
    timeout: float,
    extra_callbacks: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Serve the manifest form locally, send the browser to GitHub, wait for the callback."""
    state = secrets.token_urlsafe(16)
    server = _CallbackServer(("127.0.0.1", port), {}, manifest_target(state, org), state)
    local = f"http://127.0.0.1:{server.server_address[1]}"
    server.manifest = build_manifest(
        url=url, redirect_url=f"{local}/callback", name=name, extra_callbacks=extra_callbacks
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(f"Open {local}/ in your browser to create the App (it will continue on GitHub).")
        if open_browser:
            webbrowser.open(f"{local}/")
        deadline = time.monotonic() + timeout
        while server.conversion is None and server.error is None and time.monotonic() < deadline:
            time.sleep(0.25)
    finally:
        server.shutdown()
    if server.error:
        raise SystemExit(server.error)
    if server.conversion is None:
        raise SystemExit("timed out waiting for GitHub; run the script again")
    return server.conversion


# --- installation ----------------------------------------------------------------------


def app_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256"
    )


def list_installations(app_id: str, private_key: str) -> list[dict[str, Any]]:
    response = httpx.get(
        f"{GITHUB_API_URL}/app/installations",
        headers={
            "Authorization": f"Bearer {app_jwt(app_id, private_key)}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return [i for i in response.json() if isinstance(i, dict)]


def wait_for_installation(
    app_id: str, private_key: str, *, timeout: float, poll: float = 5.0, fetch=list_installations
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        installations = fetch(app_id, private_key)
        if installations:
            return installations[0]
        time.sleep(poll)
    return None


# --- main ------------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="public URL of the Open SWE deployment")
    parser.add_argument("--name", default="Open SWE", help="App name; unique across GitHub")
    parser.add_argument(
        "--org", default="", help="create under this organization instead of your account"
    )
    parser.add_argument("--env-file", help="write the values to this .env file")
    parser.add_argument(
        "--deployment", help="LangGraph Platform deployment id to write the values into"
    )
    parser.add_argument(
        "--control-plane",
        default=DEFAULT_CONTROL_PLANE,
        help="LangGraph Platform control-plane API",
    )
    parser.add_argument(
        "--tenant-id",
        default=ENV.LANGSMITH_TENANT_ID.optional() or "",
        help="LangSmith workspace of the deployment, when the API key spans several",
    )
    parser.add_argument(
        "--callback-url",
        action="append",
        default=[],
        help="extra dashboard login callback origin, e.g. http://localhost:2024",
    )
    parser.add_argument("--port", type=int, default=0, help="local callback port (default: random)")
    parser.add_argument(
        "--no-browser", action="store_true", help="print the URLs instead of opening them"
    )
    parser.add_argument(
        "--skip-install", action="store_true", help="do not wait for the installation"
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait at each step")
    args = parser.parse_args(argv)
    if bool(args.env_file) == bool(args.deployment):
        parser.error("pass exactly one of --env-file or --deployment")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.deployment:
        api_key = ENV.LANGSMITH_API_KEY.optional()
        if not api_key:
            sys.exit("set LANGSMITH_API_KEY (a key for the workspace that owns the deployment)")
        sink: EnvFileSink | DeploymentSink = DeploymentSink(
            args.control_plane, args.deployment, api_key, args.tenant_id
        )
    else:
        sink = EnvFileSink(Path(args.env_file))

    if not is_public_url(args.url):
        print(
            f"{args.url} is not reachable from GitHub, so the App is created without a webhook "
            "or event subscriptions. Add them in the App's settings once you have a public URL."
        )
    extra = tuple(f"{c.rstrip('/')}/dashboard/api/auth/callback" for c in args.callback_url)
    conversion = run_manifest_flow(
        url=args.url,
        name=args.name,
        org=args.org,
        port=args.port,
        open_browser=not args.no_browser,
        timeout=args.timeout,
        extra_callbacks=extra,
    )
    values = credentials_from_conversion(conversion)
    sink.write(values)
    app_url = str(conversion.get("html_url") or "")
    print(
        f"Created {conversion.get('name')} ({app_url}); wrote {', '.join(values)} to {sink.describe()}."
    )
    if not conversion.get("webhook_secret"):
        print(
            "No webhook yet: in the App's settings set the webhook URL to <public URL>/webhooks/github,"
        )
        print(
            "the secret to the GITHUB_WEBHOOK_SECRET just written, and subscribe to:",
            ", ".join(EVENTS),
        )

    if args.skip_install:
        print(
            f"Install the App at {app_url}/installations/new, then set GITHUB_APP_INSTALLATION_ID."
        )
        return 0
    print(f"Install the App on the repositories Open SWE may work in: {app_url}/installations/new")
    if not args.no_browser:
        webbrowser.open(f"{app_url}/installations/new")
    installation = wait_for_installation(
        values["GITHUB_APP_ID"], values["GITHUB_APP_PRIVATE_KEY"], timeout=args.timeout
    )
    if installation is None:
        print(
            "No installation seen yet. Install the App, then set GITHUB_APP_INSTALLATION_ID to its id."
        )
        return 1
    sink.write({"GITHUB_APP_INSTALLATION_ID": str(installation["id"])})
    account = (installation.get("account") or {}).get("login", "?")
    print(
        f"Installed on {account} (installation {installation['id']}); wrote GITHUB_APP_INSTALLATION_ID."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
