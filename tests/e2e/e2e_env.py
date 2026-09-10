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

# Fixed Linear identifiers. The team name is deliberately absent from
# LINEAR_TEAM_TO_REPO so a Linear trigger resolves the repo the way an unmapped
# workspace does: through the team-wide default (DEFAULT_REPO_OWNER/NAME below).
LINEAR_ORG_ID = "org-e2e"
LINEAR_TEAM = {"id": "team-e2e", "name": "E2E Delivery", "key": "E2E"}
LINEAR_APP_USER_ID = "linear-app-user"
LINEAR_ACCESS_TOKEN = "linear-e2e-token"
# Marker the harness puts in the text that triggers a Linear run, so the scripted
# model recognises the turn it should implement a change for.
LINEAR_TASK_MARKER = "E2E_LINEAR"

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
    # Environment scripts write themselves and their logs here. The default,
    # /open-swe/environment, assumes a sandbox where the agent is root; this
    # provider runs on the developer's own machine, whose root is not writable.
    "OPENSWE_SCRIPT_ROOT": str(TMP / "open-swe" / "environment"),
    # Keep git's --global writes (bot identity) out of the user's ~/.gitconfig.
    "GIT_CONFIG_GLOBAL": str(TMP / "gitconfig-global"),
    "GIT_CONFIG_SYSTEM": "/dev/null",
    # Path the scripted agent clones from (a local bare repo = "fake GitHub").
    "E2E_REMOTE": str(BARE_REMOTE),
    "E2E_SECOND_REMOTE": str(SECOND_BARE_REMOTE),
    # Webhook signing + bot identity.
    "GITHUB_WEBHOOK_SECRET": "test-github-secret",
    "LINEAR_WEBHOOK_SECRET": "test-linear-secret",
    # App mode: the Linear client mints an actor token instead of sending an API key.
    "LINEAR_OAUTH_CLIENT_ID": "linear-e2e-client",
    "LINEAR_OAUTH_CLIENT_SECRET": "linear-e2e-client-secret",
    # The platform's run-completion webhook, which is where a Linear session's
    # terminal response/error activity comes from. langgraph.e2e.json opts the
    # dev server into loopback webhook targets so it can reach this harness.
    "RUN_COMPLETE_WEBHOOK_SECRET": "test-run-complete-secret",
    "COMPLETION_WEBHOOK_URL": f"{BASE_URL}/webhooks/run-complete",
    "SLACK_SIGNING_SECRET": "test-slack-secret",
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "SLACK_BOT_USER_ID": BOT_USER_ID,
    "SLACK_BOT_USERNAME": BOT_USERNAME,
    "SLACK_TEAM_ID": "T_TEST",
    # Slack runs resolve the repo from this when the channel/thread carry none.
    "DEFAULT_REPO_OWNER": OWNER,
    "DEFAULT_REPO_NAME": REPO,
    # Bot-token-only mode: lets Slack runs proceed without a per-user OAuth token.
    # Tracing and the platform metadata loop stay off: the key is not real.
    "LANGSMITH_API_KEY": "test-bot-mode",
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_CONTROL_PLANE_API_KEY": "",
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
_DEFAULTS["ALLOWED_GITHUB_USERS"] = ",".join(
    [*(user["login"] for user in TEST_USERS), "thread-tools-e2e", "threads-workspace-e2e"]
)
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
FAKE_LINEAR_API = f"{BASE_URL}/fake-linear"
