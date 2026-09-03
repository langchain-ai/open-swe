"""Shared environment + constants for the full-flow E2E.

Imported FIRST by both the agent graph entrypoint and the HTTP harness, before
any ``agent.*`` module — several webapp/auth/slack constants are read into module
globals at import time, so the env must be set beforehand.

Everything here only configures *boundaries* (which sandbox, which fake API
URLs, where git writes its global config). The agent code itself is unchanged.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TMP = Path(os.environ.setdefault("E2E_TMP", str(Path(__file__).parent / ".e2e-tmp")))

# Demo repo the fake GitHub serves and the agent operates on.
OWNER = "fakeorg"
REPO = "demo"
BASE_BRANCH = "main"
FEATURE_BRANCH = "add-greet"
PR_TITLE = "Add greet() helper"
FEATURE_FILE = "greet.py"
SECOND_OWNER = "anotherorg"
SECOND_REPO = "companion"
SECOND_FEATURE_BRANCH = "add-integration"
SECOND_PR_TITLE = "Add companion integration"

# Fixed Slack identifiers so the mock UI and assertions are deterministic.
BOT_USER_ID = "U0BOT"
BOT_USERNAME = "open-swe"
DEMO_CHANNEL = "C_DEMO"
HUMAN_USER = "U_HUMAN"

PORT = os.environ.setdefault("E2E_PORT", "2024")
BASE_URL = os.environ.setdefault("E2E_BASE", f"http://127.0.0.1:{PORT}")

# Where a browser reaches the dashboard: the app's own server, not this harness.
# It is a separate origin from the backend here only because the two run as
# separate processes; the browser sees the single origin a deployment gives it.
APP_URL = os.environ.get("E2E_UI_SERVER", "http://127.0.0.1:3100").rstrip("/")

_GH_DIR = TMP / "github"
_WORK_DIR = TMP / "work"
BARE_REMOTE = _GH_DIR / f"{OWNER}__{REPO}.git"
SECOND_BARE_REMOTE = _GH_DIR / f"{SECOND_OWNER}__{SECOND_REPO}.git"

_DEFAULTS = {
    # Sandbox: real local provider, rooted in a throwaway temp dir.
    "SANDBOX_TYPE": "local",
    "LOCAL_SANDBOX_ROOT_DIR": str(_WORK_DIR),
    # Keep git's --global writes (bot identity) out of the user's ~/.gitconfig.
    "GIT_CONFIG_GLOBAL": str(TMP / "gitconfig-global"),
    "GIT_CONFIG_SYSTEM": "/dev/null",
    # Path the scripted agent clones from (a local bare repo = "fake GitHub").
    "E2E_REMOTE": str(BARE_REMOTE),
    "E2E_SECOND_REMOTE": str(SECOND_BARE_REMOTE),
    # Webhook signing + bot identity.
    "GITHUB_WEBHOOK_SECRET": "test-github-secret",
    "SLACK_SIGNING_SECRET": "test-slack-secret",
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "SLACK_BOT_USER_ID": BOT_USER_ID,
    "SLACK_BOT_USERNAME": BOT_USERNAME,
    "SLACK_TEAM_ID": "T_TEST",
    # Slack runs resolve the repo from this when the channel/thread carry none.
    "DEFAULT_REPO_OWNER": OWNER,
    "DEFAULT_REPO_NAME": REPO,
    # Bot-token-only mode: lets Slack runs proceed without a per-user OAuth token.
    "LANGSMITH_API_KEY_PROD": "test-bot-mode",
    # SDK client target (same dev server).
    "LANGGRAPH_URL": BASE_URL,
    # Dashboard: the "Open in Web" link target + session-cookie signing. Use
    # 127.0.0.1 (not localhost) so the local-dev LLM-key check stays skipped.
    # The link points at the app server, so following it lands the browser on
    # the origin a real user is on rather than on the backend.
    "DASHBOARD_BASE_URL": APP_URL,
    "DASHBOARD_API_BASE_URL": BASE_URL,
    "DASHBOARD_ALLOWED_ORIGINS": f"{APP_URL},{BASE_URL},open-swe://app",
    "DASHBOARD_JWT_SECRET": "test-dashboard-jwt-secret",
}

# Named test users (the Slack sender dropdown + the dashboard login picker, and
# the identities the automated tests log in as). Each maps a Slack sender id to
# a dashboard login with a matching email, so the Slack thread's owner (resolved
# by email) is the same person when they sign in. The first (Alice) is the
# default Slack sender, hence the default thread owner.
TEST_USERS = [
    {"name": "Alice", "slack_id": "U_ALICE", "login": "alice", "email": "alice@example.com"},
    {"name": "Bob", "slack_id": "U_BOB", "login": "bob", "email": "bob@example.com"},
]

# Alice is the workspace admin (so admin threads + the environments dashboard are
# reachable); Bob is a plain member, which is what the deny-side assertions use.
ADMIN_USER = TEST_USERS[0]
_DEFAULTS["CONFIGURED_ADMINS"] = ADMIN_USER["email"]

# The default Slack sender / thread owner; a session with this email may continue
# the thread on the web. Any other logged-in user is read-only.
SAME_USER = {"login": TEST_USERS[0]["login"], "email": TEST_USERS[0]["email"]}
OTHER_USER = {"login": TEST_USERS[1]["login"], "email": TEST_USERS[1]["email"]}

for _k, _v in _DEFAULTS.items():
    os.environ.setdefault(_k, _v)

for _d in (TMP, _GH_DIR, _WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

FAKE_GITHUB_API = f"{BASE_URL}/fake-gh"
FAKE_SLACK_API = f"{BASE_URL}/fake-slack"
