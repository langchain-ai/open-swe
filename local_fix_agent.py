from openai import OpenAI
from pathlib import Path, PurePosixPath
import ast
import atexit
import argparse
import difflib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from typing import Sequence
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

MODEL = "qwen3-coder:30b"
DEFAULT_MAX_STEPS = 40
DEFAULT_MAX_FILE_CHARS = 20000
MAX_COMMIT_PATHS = 50
MAX_COMMIT_DIFF_BYTES = 100 * 1024
MAX_REPEATED_FAILURES = 3
RECENT_SCORE_HISTORY = 3
MAX_SEARCHES_PER_ATTEMPT = 3
MAX_SEARCH_MATCHES = 20
MAX_SEARCH_OUTPUT_CHARS = 4000
MEMORY_FILE_NAME = ".fix_agent_memory.json"
SCRIPT_PATTERN_MEMORY_FILE_NAME = ".fix_agent_pattern_memory.json"
SCRIPT_PATTERN_EFFECTIVENESS_FILE_NAME = ".fix_agent_pattern_effectiveness.json"
SCRIPT_PATTERN_CONTROL_FILE_NAME = ".fix_agent_pattern_controls.json"
PATTERN_EVAL_FILE_NAME = ".fix_agent_pattern_eval.json"
PATTERN_REPO_SOURCE_CATALOG_FILE_NAME = "pattern_sources.json"
DEFAULT_PATTERN_REPO_DIR_NAME = "local_fix_agent_pattern_repo"
PATTERN_TRUST_LEVELS = {"trusted", "experimental"}
METRICS_FILE_NAME = ".fix_agent_metrics.json"
CONFIG_FILE_NAME = ".fix_agent_config.json"
RECENT_STATE_FILE_NAME = ".fix_agent_recent.json"
DOCS_STATE_FILE_NAME = ".fix_agent_docs_state.json"
PUBLISH_STATE_FILE_NAME = ".ai_publish_state.json"
RUN_ARTIFACTS_DIR_NAME = ".fix_agent_runs"
PRIMARY_OPERATOR_DOC_FILES = [
    "README.md",
    "docs/RUNBOOK.md",
    "docs/TROUBLESHOOTING.md",
]
RUN_MODES = {
    "quick": {"max_steps": 20, "max_file_chars": 12000},
    "safe": {"max_steps": 40, "max_file_chars": 20000},
    "deep": {"max_steps": 70, "max_file_chars": 30000},
    "benchmark": {"max_steps": 55, "max_file_chars": 25000},
}
STRATEGY_MINIMAL_PATCH = "minimal_patch"
STRATEGY_TEST_FIRST_DIAGNOSIS = "test_first_diagnosis"
STRATEGY_BROADER_REWRITE = "broader_rewrite"
FAILURE_SYNTAX_ERROR = "syntax_error"
FAILURE_IMPORT_ERROR = "import_error"
FAILURE_ASSERTION_FAILURE = "assertion_failure"
FAILURE_RUNTIME_ERROR = "runtime_error"
FAILURE_UNKNOWN = "unknown"
FAILURE_RANK = {
    FAILURE_UNKNOWN: 0,
    FAILURE_RUNTIME_ERROR: 1,
    FAILURE_IMPORT_ERROR: 2,
    FAILURE_SYNTAX_ERROR: 2,
    FAILURE_ASSERTION_FAILURE: 3,
}
DOMAIN_PATTERN_FAMILIES = {
    "proxy": {"proxy_handling", "request_session", "retry_backoff", "timeout", "rate_limit_handling", "error_handling"},
    "network": {"proxy_handling", "request_session", "retry_backoff", "timeout", "rate_limit_handling", "error_handling"},
    "cli": {"cli_style", "entrypoint"},
    "streaming": {"request_session", "timeout", "error_handling", "rate_limit_handling"},
    "scraping": {"request_session", "retry_backoff", "rate_limit_handling", "proxy_handling", "timeout"},
    "local_utils": {"function_organization", "validation_strategy", "config_loading"},
}

client = OpenAI(
    base_url="http://127.0.0.1:11434/v1",
    api_key="ollama",
)

IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", ".mypy_cache", ".ruff_cache", "dist", "build"
}

ALLOWED_COMMAND_PREFIXES = [
    "pytest",
    "python -m pytest",
    "python ",
    "python3 ",
    "ls",
    "cat",
    "grep",
    "ruff",
    "flake8",
]

CURRENT_SUBPROCESS_ENV: dict[str, str] = {}
CURRENT_REMOTE_TARGET = ""
CURRENT_REMOTE_REPO: Path | None = None
CURRENT_REMOTE_CONTROL_PATH = ""
CURRENT_REMOTE_CONTROL_DIR = ""
CURRENT_REMOTE_SESSION_ACTIVE = False
CURRENT_REMOTE_SESSION_REOPENED = False
CURRENT_REMOTE_SESSION_REGISTERED = False
REMOTE_EXECUTION_STATE: dict[str, dict | None] = {"blocked": None}
CURRENT_VALIDATION_PLAN: dict = {}
CURRENT_PUBLISH_IGNORE_PATHS: set[str] = set()
CURRENT_NO_AUTO_CONFLICT_RESOLUTION_AFTER_SYNC = False
BUILT_IN_PUBLISH_IGNORE_PATHS = {
    MEMORY_FILE_NAME,
    METRICS_FILE_NAME,
    RECENT_STATE_FILE_NAME,
    DOCS_STATE_FILE_NAME,
    PUBLISH_STATE_FILE_NAME,
}
API_SAFETY_STATE = {
    "proxy_enabled": False,
    "http_proxy": "",
    "https_proxy": "",
    "run_budget": 0,
    "attempt_budget": 0,
    "likely_rate_limit_hits": 0,
    "attempt_rate_limit_hits": 0,
    "cooldowns_triggered": 0,
    "last_rate_limit_output": "",
}
DEFAULT_PROBE_TIMEOUT_SECONDS = 8
DEFAULT_PROBE_MAX_BYTES = 32768
DEFAULT_PROBE_FOLLOW_UP_LIMIT = 2
DEFAULT_PROBE_USER_AGENT = "local-fix-agent/1.0"
PROBE_SECRET_NAME_RE = re.compile(r"(?i)(token|key|secret|password|passwd|cookie|auth|signature|credential|session)")
DEFAULT_PATTERN_REPO_INCLUDE_GLOBS = ["*.py"]
CONFIG_TASKS = {"validate", "cleanup", "compare", "generate", "align"}
CONFIG_TYPES = {"auto", "nginx", "reverse_proxy", "php_ini", "php_fpm_pool"}
DEFAULT_PATTERN_REPO_MAX_FILES = 200


def is_likely_rate_limited(output: str) -> bool:
    lowered = (output or "").lower()
    return any(
        token in lowered
        for token in [
            " 429",
            "429 ",
            "rate limit",
            "too many requests",
            "retry-after",
            "temporarily unavailable",
        ]
    )


def configure_subprocess_safety(settings: dict) -> None:
    CURRENT_SUBPROCESS_ENV.clear()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
        value = settings.get(key, "")
        if value:
            CURRENT_SUBPROCESS_ENV[key] = value
            CURRENT_SUBPROCESS_ENV[key.lower()] = value
    API_SAFETY_STATE["proxy_enabled"] = bool(settings.get("HTTP_PROXY") or settings.get("HTTPS_PROXY") or settings.get("ALL_PROXY"))
    API_SAFETY_STATE["http_proxy"] = settings.get("HTTP_PROXY", "")
    API_SAFETY_STATE["https_proxy"] = settings.get("HTTPS_PROXY", "")
    API_SAFETY_STATE["run_budget"] = int(settings.get("run_budget", 0) or 0)
    API_SAFETY_STATE["attempt_budget"] = int(settings.get("attempt_budget", 0) or 0)
    API_SAFETY_STATE["likely_rate_limit_hits"] = 0
    API_SAFETY_STATE["attempt_rate_limit_hits"] = 0
    API_SAFETY_STATE["cooldowns_triggered"] = 0
    API_SAFETY_STATE["last_rate_limit_output"] = ""


def configure_publish_ignore_paths(config: dict) -> None:
    CURRENT_PUBLISH_IGNORE_PATHS.clear()
    configured = config.get("publish_ignore_paths", []) if isinstance(config, dict) else []
    if not isinstance(configured, list):
        return
    for item in configured:
        candidate = str(item or "").strip()
        if candidate:
            CURRENT_PUBLISH_IGNORE_PATHS.add(candidate)


def publish_ignored_change_paths() -> set[str]:
    return BUILT_IN_PUBLISH_IGNORE_PATHS | CURRENT_PUBLISH_IGNORE_PATHS


def configure_execution_target(target: str, repo: Path) -> None:
    global CURRENT_REMOTE_TARGET, CURRENT_REMOTE_REPO, CURRENT_REMOTE_SESSION_REOPENED
    CURRENT_REMOTE_TARGET = target.strip()
    CURRENT_REMOTE_REPO = repo if CURRENT_REMOTE_TARGET else None
    CURRENT_REMOTE_SESSION_REOPENED = False


def is_remote_repo(repo: Path) -> bool:
    return bool(CURRENT_REMOTE_TARGET and CURRENT_REMOTE_REPO and repo == CURRENT_REMOTE_REPO)


def remote_repo_path(repo: Path, rel_path: str) -> str:
    rel = rel_path.strip()
    if not rel or rel.startswith("/"):
        raise RuntimeError(f"Refusing outside repo: {rel_path}")
    posix = PurePosixPath(rel)
    if ".." in posix.parts:
        raise RuntimeError(f"Refusing outside repo: {rel_path}")
    return str(PurePosixPath(str(repo)) / posix)


def state_storage_path(repo: Path, name: str) -> Path:
    if not is_remote_repo(repo):
        return repo / name
    root = script_state_path(".fix_agent_remote_state")
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{CURRENT_REMOTE_TARGET}_{repo}")
    if name == RUN_ARTIFACTS_DIR_NAME:
        return root / slug / name
    return root / f"{slug}_{name}"


def ssh_transport_args() -> list[str]:
    if not CURRENT_REMOTE_CONTROL_PATH:
        return []
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={CURRENT_REMOTE_CONTROL_PATH}",
        "-o",
        "ControlPersist=3600",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def build_remote_control_path(target: str) -> tuple[str, str]:
    root = Path(tempfile.gettempdir()) / "lfa_ssh"
    root.mkdir(parents=True, exist_ok=True)
    target_slug = re.sub(r"[^A-Za-z0-9]+", "_", target).strip("_").lower() or "remote"
    target_slug = target_slug[:16]
    run_token = f"{os.getpid()}_{secrets.token_hex(3)}"
    control_dir = root / f"{target_slug}_{run_token}"
    control_dir.mkdir(parents=True, exist_ok=True)
    control_path = control_dir / f"ctl_{target_slug}.sock"
    return str(control_dir), str(control_path)


def set_remote_blocked(blocked: dict | None) -> None:
    REMOTE_EXECUTION_STATE["blocked"] = blocked


def classify_remote_issue(
    output: str,
    target: str,
    repo: Path | None = None,
    command: str = "",
    stage: str = "command",
) -> dict | None:
    text = (output or "").strip()
    lowered = text.lower()
    command_text = command or "(remote command)"
    repo_text = str(repo) if repo else ""

    if any(token in lowered for token in [
        "could not resolve hostname",
        "name or service not known",
        "no route to host",
        "network is unreachable",
        "connection timed out",
        "operation timed out",
        "connection refused",
        "failed to connect",
        "tailscale",
    ]):
        return {
            "kind": "connectivity issue",
            "reason": "remote connectivity issue",
            "evidence": text or f"Could not reach remote host {target}",
            "needs": f"network reachability to {target} and a working SSH route",
            "action": f"Verify `ssh {target}` works from this machine and confirm VPN/Tailscale connectivity if required.",
        }

    if "permission denied (publickey" in lowered or "permission denied, please try again" in lowered or "authentication failed" in lowered:
        return {
            "kind": "auth issue",
            "reason": "remote SSH auth issue",
            "evidence": text or f"SSH authentication failed for {target}",
            "needs": f"valid SSH credentials for {target}",
            "action": f"Verify your SSH key/agent access with `ssh {target}` before rerunning.",
        }

    if stage == "repo_check" and repo_text and ("no such file or directory" in lowered or "can't cd to" in lowered or "cannot access" in lowered):
        return {
            "kind": "repo/path issue",
            "reason": "remote repo path not found",
            "evidence": text or f"Remote repo path {repo_text} was not found on {target}",
            "needs": f"an existing repo at {repo_text} on {target}",
            "action": f"Confirm the repo path on {target} and rerun with `--repo {repo_text}`.",
        }

    if "permission denied" in lowered:
        if stage == "file_write":
            return {
                "kind": "auth issue",
                "reason": "remote file write permission issue",
                "evidence": text or f"Writing remote files failed on {target}",
                "needs": "write access to the target repo files on the remote host",
                "action": "Check file ownership and write permissions on the remote repo path.",
            }
        if stage in {"repo_check", "command"} and repo_text:
            return {
                "kind": "repo/path issue",
                "reason": "remote repo path permission issue",
                "evidence": text or f"Access to remote repo path {repo_text} was denied on {target}",
                "needs": f"read/write permissions for {repo_text} on {target}",
                "action": "Check repo directory permissions on the remote host and rerun.",
            }
        return {
            "kind": "auth issue",
            "reason": "remote SSH auth issue",
            "evidence": text or f"SSH permission denied for {target}",
            "needs": f"valid SSH access to {target}",
            "action": f"Verify `ssh {target}` succeeds and that the SSH user has access to the repo.",
        }

    if "no such file or directory" in lowered and repo_text and repo_text in text:
        return {
            "kind": "repo/path issue",
            "reason": "remote repo path not found",
            "evidence": text,
            "needs": f"an existing repo at {repo_text} on {target}",
            "action": f"Confirm the repo path on {target} and rerun with `--repo {repo_text}`.",
        }

    if "timed out" in lowered and command_text:
        return {
            "kind": "timeout issue",
            "reason": "remote command timed out",
            "evidence": text or f"Remote command timed out on {target}: {command_text}",
            "needs": "a responsive remote host and a command that completes within the timeout window",
            "action": f"Check remote load, rerun the command manually on {target}, or narrow the test target.",
        }

    return None


def remote_session_failed(output: str) -> bool:
    lowered = (output or "").lower()
    return any(
        token in lowered
        for token in [
            "control socket",
            "master is dead",
            "broken pipe",
            "connection reset",
            "connection closed",
        ]
    )


def close_remote_session() -> None:
    global CURRENT_REMOTE_CONTROL_PATH, CURRENT_REMOTE_CONTROL_DIR, CURRENT_REMOTE_SESSION_ACTIVE
    if CURRENT_REMOTE_TARGET and CURRENT_REMOTE_CONTROL_PATH and CURRENT_REMOTE_SESSION_ACTIVE:
        proc = subprocess.run(
            ["ssh", *ssh_transport_args(), "-O", "exit", CURRENT_REMOTE_TARGET],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(f"Closed persistent remote session: {CURRENT_REMOTE_TARGET}")
    CURRENT_REMOTE_SESSION_ACTIVE = False
    if CURRENT_REMOTE_CONTROL_PATH:
        try:
            Path(CURRENT_REMOTE_CONTROL_PATH).unlink()
        except FileNotFoundError:
            pass
    if CURRENT_REMOTE_CONTROL_DIR:
        shutil.rmtree(CURRENT_REMOTE_CONTROL_DIR, ignore_errors=True)
    CURRENT_REMOTE_CONTROL_PATH = ""
    CURRENT_REMOTE_CONTROL_DIR = ""


def open_remote_session() -> tuple[bool, str]:
    global CURRENT_REMOTE_CONTROL_PATH, CURRENT_REMOTE_CONTROL_DIR, CURRENT_REMOTE_SESSION_ACTIVE
    global CURRENT_REMOTE_SESSION_REGISTERED
    if not CURRENT_REMOTE_TARGET:
        return True, ""
    if CURRENT_REMOTE_SESSION_ACTIVE and CURRENT_REMOTE_CONTROL_PATH:
        return True, ""
    if CURRENT_REMOTE_CONTROL_DIR:
        shutil.rmtree(CURRENT_REMOTE_CONTROL_DIR, ignore_errors=True)
    CURRENT_REMOTE_CONTROL_DIR, CURRENT_REMOTE_CONTROL_PATH = build_remote_control_path(CURRENT_REMOTE_TARGET)
    try:
        Path(CURRENT_REMOTE_CONTROL_PATH).unlink()
    except FileNotFoundError:
        pass
    try:
        proc = subprocess.run(
            ["ssh", *ssh_transport_args(), "-MNf", CURRENT_REMOTE_TARGET],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        blocked = {
            "kind": "timeout issue",
            "reason": "remote command timed out",
            "evidence": f"Timed out while opening the SSH session to {CURRENT_REMOTE_TARGET}",
            "needs": f"a responsive SSH endpoint at {CURRENT_REMOTE_TARGET}",
            "action": f"Verify `ssh {CURRENT_REMOTE_TARGET}` responds quickly and rerun.",
        }
        set_remote_blocked(blocked)
        close_remote_session()
        return False, blocked["evidence"]
    if proc.returncode != 0:
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        set_remote_blocked(classify_remote_issue(output, CURRENT_REMOTE_TARGET, CURRENT_REMOTE_REPO, stage="open_session"))
        close_remote_session()
        return False, output
    CURRENT_REMOTE_SESSION_ACTIVE = True
    set_remote_blocked(None)
    if not CURRENT_REMOTE_SESSION_REGISTERED:
        atexit.register(close_remote_session)
        CURRENT_REMOTE_SESSION_REGISTERED = True
    print(f"Opened persistent remote session: {CURRENT_REMOTE_TARGET}")
    print("Connection reuse: active")
    print("SSH keepalive: ControlPersist=3600s, ServerAliveInterval=15, ServerAliveCountMax=3")
    return True, ""


def check_remote_repo_access(repo: Path) -> tuple[bool, dict | None]:
    if not CURRENT_REMOTE_TARGET:
        return True, None
    try:
        proc = subprocess.run(
            [
                "ssh",
                *ssh_transport_args(),
                CURRENT_REMOTE_TARGET,
                f"test -d {shlex.quote(str(repo))}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        blocked = {
            "kind": "timeout issue",
            "reason": "remote command timed out",
            "evidence": f"Remote repo access check timed out on {CURRENT_REMOTE_TARGET} for {repo}",
            "needs": "a responsive remote host",
            "action": f"Verify `{repo}` on {CURRENT_REMOTE_TARGET}` is reachable and rerun.",
        }
        set_remote_blocked(blocked)
        return False, blocked
    if proc.returncode == 0:
        return True, None
    blocked = classify_remote_issue(output, CURRENT_REMOTE_TARGET, repo, stage="repo_check")
    if not blocked:
        blocked = {
            "kind": "repo/path issue",
            "reason": "remote repo path not found",
            "evidence": output or f"Could not access {repo} on {CURRENT_REMOTE_TARGET}",
            "needs": f"an existing accessible repo at {repo} on {CURRENT_REMOTE_TARGET}",
            "action": f"Confirm the repo path on {CURRENT_REMOTE_TARGET} and rerun.",
        }
    set_remote_blocked(blocked)
    return False, blocked


def ensure_remote_session() -> tuple[bool, str]:
    if not CURRENT_REMOTE_TARGET:
        return True, ""
    return open_remote_session()


def run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
    global CURRENT_REMOTE_SESSION_ACTIVE, CURRENT_REMOTE_SESSION_REOPENED
    env = os.environ.copy()
    env.update(CURRENT_SUBPROCESS_ENV)
    if is_remote_repo(cwd):
        ok, error = ensure_remote_session()
        if not ok:
            return 255, f"Remote session unavailable: {error}".strip()
        env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in CURRENT_SUBPROCESS_ENV.items() if key.isupper())
        if shell:
            inner = command
        else:
            inner = " ".join(shlex.quote(part) for part in command)
        remote_cmd = f"cd {shlex.quote(str(cwd))} && {inner}"
        if env_prefix:
            remote_cmd = f"export {env_prefix} && {remote_cmd}"
        try:
            proc = subprocess.run(
                ["ssh", *ssh_transport_args(), CURRENT_REMOTE_TARGET, remote_cmd],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            blocked = {
                "kind": "timeout issue",
                "reason": "remote command timed out",
                "evidence": f"Target {CURRENT_REMOTE_TARGET}, command: {inner[:200]}",
                "needs": "a responsive remote host and a shorter-running remote command",
                "action": "Retry on a responsive host or narrow the test/command scope.",
            }
            set_remote_blocked(blocked)
            return 124, blocked["evidence"]
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and remote_session_failed(output) and not CURRENT_REMOTE_SESSION_REOPENED:
            CURRENT_REMOTE_SESSION_ACTIVE = False
            CURRENT_REMOTE_SESSION_REOPENED = True
            progress("remote session dropped, reopening once...")
            ok, error = ensure_remote_session()
            if not ok:
                blocked = {
                    "kind": "remote session drop",
                    "reason": "remote session dropped",
                    "evidence": error or output.strip(),
                    "needs": f"a stable SSH session to {CURRENT_REMOTE_TARGET}",
                    "action": f"Verify `ssh {CURRENT_REMOTE_TARGET}` is stable and rerun.",
                }
                set_remote_blocked(blocked)
                return 255, blocked["evidence"]
            try:
                proc = subprocess.run(
                    ["ssh", *ssh_transport_args(), CURRENT_REMOTE_TARGET, remote_cmd],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                blocked = {
                    "kind": "timeout issue",
                    "reason": "remote command timed out",
                    "evidence": f"Target {CURRENT_REMOTE_TARGET}, command: {inner[:200]}",
                    "needs": "a responsive remote host and a shorter-running remote command",
                    "action": "Retry on a responsive host or narrow the test/command scope.",
                }
                set_remote_blocked(blocked)
                return 124, blocked["evidence"]
        if proc.returncode != 0:
            blocked = classify_remote_issue(output, CURRENT_REMOTE_TARGET, cwd, inner, stage="command")
            if blocked:
                set_remote_blocked(blocked)
    else:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=shell,
            capture_output=True,
            text=True,
            env=env,
        )
    output = (proc.stdout or "") + (proc.stderr or "")
    if is_likely_rate_limited(output):
        API_SAFETY_STATE["likely_rate_limit_hits"] += 1
        API_SAFETY_STATE["attempt_rate_limit_hits"] += 1
        API_SAFETY_STATE["last_rate_limit_output"] = output.strip()
        run_budget = API_SAFETY_STATE.get("run_budget", 0)
        attempt_budget = API_SAFETY_STATE.get("attempt_budget", 0)
        over_attempt_budget = attempt_budget and API_SAFETY_STATE["attempt_rate_limit_hits"] >= attempt_budget
        over_run_budget = run_budget and API_SAFETY_STATE["likely_rate_limit_hits"] >= run_budget
        if over_attempt_budget or over_run_budget or API_SAFETY_STATE["likely_rate_limit_hits"] >= 2:
            API_SAFETY_STATE["cooldowns_triggered"] += 1
            progress("rate-limit signal detected, cooling down briefly...")
            time.sleep(2)
    return proc.returncode, output.strip()


def progress(message: str) -> None:
    print(f"→ {message}", flush=True)


def startup_signal(message: str) -> None:
    print(f"⚡ {message}", flush=True)


def success_signal(message: str) -> None:
    print(f"✔ {message}", flush=True)


def safe_repo_path(repo: Path, rel_path: str) -> Path:
    if is_remote_repo(repo):
        remote_repo_path(repo, rel_path)
        return Path(str(repo / rel_path))
    target = (repo / rel_path).resolve()
    try:
        target.relative_to(repo)
    except ValueError:
        raise RuntimeError(f"Refusing outside repo: {target}")
    return target


def repo_path_exists(repo: Path, rel_path: str) -> bool:
    if is_remote_repo(repo):
        remote_path = remote_repo_path(repo, rel_path)
        code, _ = run_subprocess(
            f"test -e {shlex.quote(remote_path)}",
            repo,
            shell=True,
        )
        return code == 0
    return safe_repo_path(repo, rel_path).exists()


def is_git_repo(repo: Path) -> bool:
    try:
        code, _ = run_subprocess(["git", "rev-parse", "--is-inside-work-tree"], repo)
    except OSError:
        return False
    return code == 0


def detect_current_repo(start: Path) -> Path | None:
    code, output = run_subprocess(["git", "rev-parse", "--show-toplevel"], start)
    if code == 0 and output.strip():
        return Path(output.strip()).resolve()
    return None


def script_state_path(name: str) -> Path:
    return Path(__file__).resolve().with_name(name)


def current_git_branch(repo: Path) -> str:
    code, output = run_subprocess(["git", "branch", "--show-current"], repo)
    return output.strip() if code == 0 else ""


def backup_file(target: Path) -> None:
    backup = target.with_suffix(target.suffix + ".bak")
    if target.exists() and not backup.exists():
        backup.write_text(target.read_text())


def extract_status_path(line: str) -> str:
    body = line[2:].lstrip() if len(line) > 2 else ""
    if " -> " in body:
        return body.split(" -> ", 1)[1].strip()
    return body.strip()


def is_ignored_change_path(path: str, extra_ignored_paths: set[str] | None = None) -> bool:
    if not path:
        return True
    rel = Path(path)
    path_str = str(rel)
    return (
        (extra_ignored_paths is not None and path_str in extra_ignored_paths)
        or
        path_str == MEMORY_FILE_NAME
        or path_str == PUBLISH_STATE_FILE_NAME
        or
        path_str.endswith(".bak")
        or path_str.endswith(".pyc")
        or "__pycache__/" in path_str
        or any(part in IGNORE_DIRS for part in rel.parts)
    )


def is_publish_ignored_change_path(path: str) -> bool:
    if not path:
        return True
    return is_ignored_change_path(path, extra_ignored_paths=publish_ignored_change_paths())


def raw_git_status_output(repo: Path) -> str:
    code, output = run_subprocess(["git", "status", "--short", "--untracked-files=all"], repo)
    if code != 0:
        return output
    return output.rstrip()


def filter_status_lines(
    output: str,
    ignore_all_ignored_dirs: bool = False,
    ignore_path_predicate=is_ignored_change_path,
) -> list[str]:
    filtered = []
    for line in output.splitlines():
        path = extract_status_path(line)
        if ignore_path_predicate(path):
            continue
        filtered.append(line)
    return filtered


def filtered_git_status_output(
    repo: Path,
    ignore_all_ignored_dirs: bool = False,
    ignore_path_predicate=is_ignored_change_path,
) -> str:
    output = raw_git_status_output(repo)
    try:
        return "\n".join(filter_status_lines(output, ignore_all_ignored_dirs, ignore_path_predicate))
    except TypeError:
        return "\n".join(filter_status_lines(output, ignore_all_ignored_dirs))


def meaningful_changed_paths(repo: Path, ignore_path_predicate=is_ignored_change_path) -> list[str]:
    status_output = filtered_git_status_output(repo, ignore_all_ignored_dirs=True, ignore_path_predicate=ignore_path_predicate)
    paths = []
    seen = set()
    for line in status_output.splitlines():
        path = extract_status_path(line)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def classify_git_working_tree(repo: Path, ignore_path_predicate=is_ignored_change_path) -> dict:
    try:
        status_output = filtered_git_status_output(repo, ignore_all_ignored_dirs=True, ignore_path_predicate=ignore_path_predicate)
    except TypeError:
        status_output = filtered_git_status_output(repo, ignore_all_ignored_dirs=True)
    has_unstaged = False
    has_staged = False
    has_untracked = False
    staged_paths: list[str] = []
    unstaged_paths: list[str] = []
    untracked_paths: list[str] = []
    for line in status_output.splitlines():
        path = extract_status_path(line)
        if line.startswith("??"):
            has_untracked = True
            if path and path not in untracked_paths:
                untracked_paths.append(path)
            continue
        staged_flag = line[0] if len(line) > 0 else " "
        unstaged_flag = line[1] if len(line) > 1 else " "
        if staged_flag not in {" ", "?"}:
            has_staged = True
            if path and path not in staged_paths:
                staged_paths.append(path)
        if unstaged_flag != " ":
            has_unstaged = True
            if path and path not in unstaged_paths:
                unstaged_paths.append(path)
    return {
        "status_output": status_output,
        "clean": not status_output.strip(),
        "has_unstaged": has_unstaged,
        "has_staged": has_staged,
        "has_untracked": has_untracked,
        "staged_paths": staged_paths,
        "unstaged_paths": unstaged_paths,
        "untracked_paths": untracked_paths,
    }


def git_cached_staged_paths(repo: Path, paths: list[str] | None = None) -> list[str]:
    command = ["git", "diff", "--cached", "--name-only"]
    scoped_paths = [str(path).strip() for path in (paths or []) if str(path).strip()]
    if scoped_paths:
        command.extend(["--", *scoped_paths])
    try:
        code, output = run_subprocess(command, repo)
    except Exception:
        return []
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def classify_publishable_changes(
    repo: Path,
    baseline_commit: str = "",
    current_commit: str = "HEAD",
) -> dict:
    status_output = raw_git_status_output(repo)
    diff_output = ""
    diff_files_detected: list[str] = []
    last_published_commit = str(baseline_commit or "").strip()
    current_commit_ref = str(current_commit or "HEAD").strip() or "HEAD"
    if last_published_commit:
        code, output = run_subprocess(
            ["git", "diff", "--name-status", last_published_commit, current_commit_ref],
            repo,
        )
        if code == 0:
            diff_output = output.rstrip()
            seen_diff: set[str] = set()
            for line in diff_output.splitlines():
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                path = parts[1].strip()
                if path and path not in seen_diff:
                    seen_diff.add(path)
                    diff_files_detected.append(path)
    meaningful_paths: list[str] = []
    ignored_changes: list[str] = []
    seen_meaningful: set[str] = set()
    seen_ignored: set[str] = set()
    combined_lines = []
    if diff_output:
        combined_lines.extend(diff_output.splitlines())
    if status_output:
        combined_lines.extend(status_output.splitlines())
    for line in combined_lines:
        path = extract_status_path(line)
        if not path and "\t" in line:
            parts = line.split("\t", 1)
            if len(parts) == 2:
                path = parts[1].strip()
        if not path:
            continue
        if is_publish_ignored_change_path(path):
            if path not in seen_ignored:
                seen_ignored.add(path)
                ignored_changes.append(path)
            continue
        if path not in seen_meaningful:
            seen_meaningful.add(path)
            meaningful_paths.append(path)
    return {
        "status_output": status_output,
        "diff_output": diff_output,
        "diff_files_detected": diff_files_detected,
        "last_published_commit": last_published_commit,
        "current_commit": current_commit_ref,
        "meaningful_changes_detected": bool(meaningful_paths),
        "meaningful_paths": meaningful_paths,
        "ignored_changes": ignored_changes,
    }


def publish_meaningful_changed_paths(repo: Path) -> list[str]:
    try:
        return meaningful_changed_paths(repo, ignore_path_predicate=is_publish_ignored_change_path)
    except TypeError:
        return meaningful_changed_paths(repo)


def classify_publish_working_tree(repo: Path) -> dict:
    try:
        return classify_git_working_tree(repo, ignore_path_predicate=is_publish_ignored_change_path)
    except TypeError:
        return classify_git_working_tree(repo)


PUBLISH_GENERATED_DIRS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "htmlcov",
    "site",
    RUN_ARTIFACTS_DIR_NAME,
}
PUBLISH_GENERATED_SUFFIXES = {
    ".pyc",
}
PUBLISH_ARTIFACT_SUFFIXES = {
    ".cache",
    ".diff",
    ".log",
    ".orig",
    ".out",
    ".patch",
    ".rej",
    ".temp",
    ".tmp",
    ".txt",
}
PUBLISH_CONFIG_SUFFIXES = {".cfg", ".ini", ".json", ".toml", ".yaml", ".yml"}
PUBLISH_CODE_SUFFIXES = {".js", ".jsx", ".py", ".ts", ".tsx"}
DEFAULT_PUBLISH_AUTO_REMOVE_SAFE_ARTIFACTS = True
DEFAULT_PUBLISH_AUTO_IGNORE_KNOWN_JUNK = False
DEFAULT_PUBLISH_RUN_ARTIFACT_DIRS = [RUN_ARTIFACTS_DIR_NAME]


def _publish_config_block(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    block = config.get("publish_blockers", {})
    return block if isinstance(block, dict) else {}


def _publish_config_globs(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def load_publish_blocker_policy(repo: Path, *, auto_remediate: bool = True) -> dict:
    config, _config_path = load_agent_config(None, repo)
    publish_blockers = _publish_config_block(config)
    known_junk_globs = _publish_config_globs(publish_blockers.get("known_junk_globs", []))
    safe_ignore_globs = _publish_config_globs(publish_blockers.get("safe_ignore_globs", []))
    safe_remove_globs = _publish_config_globs(publish_blockers.get("safe_remove_globs", []))
    run_artifact_dirs = _publish_config_globs(publish_blockers.get("run_artifact_dirs", DEFAULT_PUBLISH_RUN_ARTIFACT_DIRS))
    return {
        "auto_remediate": bool(auto_remediate),
        "auto_remove_safe_artifacts": bool(publish_blockers.get("auto_remove_safe_artifacts", DEFAULT_PUBLISH_AUTO_REMOVE_SAFE_ARTIFACTS)),
        "auto_ignore_known_junk": bool(publish_blockers.get("auto_ignore_known_junk", DEFAULT_PUBLISH_AUTO_IGNORE_KNOWN_JUNK)),
        "known_junk_globs": known_junk_globs,
        "safe_ignore_globs": safe_ignore_globs,
        "safe_remove_globs": safe_remove_globs,
        "run_artifact_dirs": run_artifact_dirs,
    }


def publish_path_matches_any_glob(path: str, globs: list[str]) -> str:
    normalized = str(path or "").strip()
    for pattern in globs:
        if fnmatch.fnmatch(normalized, pattern):
            return pattern
    return ""


def is_run_artifact_path(path: str, policy: dict) -> bool:
    normalized = str(path or "").strip()
    if not normalized:
        return False
    run_dirs = set(path.strip() for path in (policy.get("run_artifact_dirs") or []))
    if not run_dirs:
        return False
    rel_path = Path(normalized)
    return bool(rel_path.parts and rel_path.parts[0] in run_dirs)


def is_high_confidence_safe_artifact_path(path: str, analysis: dict, policy: dict) -> bool:
    normalized = str(path or "").strip()
    rel = Path(normalized) if normalized else Path()
    hashed_root_txt = bool(rel.parts and len(rel.parts) == 1 and re.fullmatch(r"[0-9a-f]{24,}\.txt", rel.name.lower()))
    if hashed_root_txt:
        return True
    if publish_path_matches_any_glob(normalized, list(policy.get("safe_remove_globs") or [])):
        return True
    if publish_path_matches_any_glob(normalized, list(policy.get("known_junk_globs") or [])):
        return True
    if is_run_artifact_path(normalized, policy) and str(analysis.get("file_type") or "") in {"artifact", "generated"}:
        return True
    return str(analysis.get("classification_source") or "") in {"pattern_match", "path_rule"} and str(analysis.get("file_type") or "") in {"artifact", "generated"}


def classify_publish_path(path: str) -> dict:
    normalized = str(path or "").strip()
    rel = Path(normalized) if normalized else Path()
    suffix = rel.suffix.lower()
    lowered = rel.name.lower()
    hashed_artifact = bool(re.fullmatch(r"[0-9a-f]{24,}(?:\.[a-z0-9]+)?", lowered))
    if not normalized:
        return {
            "path": "",
            "file_type": "unknown",
            "classification_source": "fallback",
            "publishable": False,
            "publish_reason": "unknown file type",
        }
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        return {
            "path": normalized,
            "file_type": "unknown",
            "classification_source": "path_rule",
            "publishable": False,
            "publish_reason": "outside allowed publish paths",
        }
    if is_publish_ignored_change_path(normalized):
        return {
            "path": normalized,
            "file_type": "state",
            "classification_source": "explicit_ignore",
            "publishable": False,
            "publish_reason": "internal state file",
        }
    if any(part in PUBLISH_GENERATED_DIRS for part in rel.parts):
        return {
            "path": normalized,
            "file_type": "generated",
            "classification_source": "path_rule",
            "publishable": False,
            "publish_reason": "generated/artifact file",
        }
    if normalized == "README.md" or normalized.startswith("docs/"):
        return {
            "path": normalized,
            "file_type": "docs",
            "classification_source": "path_rule",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if suffix == ".md":
        return {
            "path": normalized,
            "file_type": "docs",
            "classification_source": "extension",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if normalized.startswith("tests/"):
        return {
            "path": normalized,
            "file_type": "test",
            "classification_source": "path_rule",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if rel.name.startswith("test_") or rel.name.endswith("_test.py"):
        return {
            "path": normalized,
            "file_type": "test",
            "classification_source": "pattern_match",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if normalized.startswith("scripts/"):
        return {
            "path": normalized,
            "file_type": "script",
            "classification_source": "path_rule",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if suffix == ".sh":
        return {
            "path": normalized,
            "file_type": "script",
            "classification_source": "extension",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if suffix in PUBLISH_CONFIG_SUFFIXES:
        return {
            "path": normalized,
            "file_type": "config",
            "classification_source": "extension",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    if suffix in PUBLISH_GENERATED_SUFFIXES:
        return {
            "path": normalized,
            "file_type": "generated",
            "classification_source": "extension",
            "publishable": False,
            "publish_reason": "generated/artifact file",
        }
    if hashed_artifact:
        return {
            "path": normalized,
            "file_type": "artifact",
            "classification_source": "pattern_match",
            "publishable": False,
            "publish_reason": "generated/artifact file",
        }
    if suffix in PUBLISH_ARTIFACT_SUFFIXES:
        return {
            "path": normalized,
            "file_type": "artifact",
            "classification_source": "extension",
            "publishable": False,
            "publish_reason": "generated/artifact file",
        }
    if suffix in PUBLISH_CODE_SUFFIXES:
        return {
            "path": normalized,
            "file_type": "code",
            "classification_source": "extension",
            "publishable": True,
            "publish_reason": "matches code/docs/tests/config patterns",
        }
    return {
        "path": normalized,
        "file_type": "unknown",
        "classification_source": "fallback",
        "publishable": False,
        "publish_reason": "unknown file type",
    }


def collect_publish_working_tree_entries(repo: Path, status_output: str = "") -> list[dict]:
    entries: dict[str, dict] = {}
    source_output = status_output
    if not source_output:
        try:
            source_output = raw_git_status_output(repo)
        except Exception:
            source_output = ""
    for line in source_output.splitlines():
        path = extract_status_path(line)
        if not path:
            continue
        entry = entries.setdefault(
            path,
            {
                "path": path,
                "status_lines": [],
                "tracked": True,
                "staged": False,
                "unstaged": False,
                "untracked": False,
            },
        )
        entry["status_lines"].append(line)
        if line.startswith("??"):
            entry["tracked"] = False
            entry["untracked"] = True
            continue
        staged_flag = line[0] if len(line) > 0 else " "
        unstaged_flag = line[1] if len(line) > 1 else " "
        if staged_flag not in {" ", "?"}:
            entry["staged"] = True
        if unstaged_flag != " ":
            entry["unstaged"] = True
    enriched: list[dict] = []
    for path in sorted(entries):
        item = dict(entries[path])
        item.update(classify_publish_path(path))
        enriched.append(item)
    return enriched


def build_publish_file_decisions(
    entries: list[dict],
    *,
    expected_paths: list[str] | None = None,
    staged_paths: list[str] | None = None,
    remaining_paths: list[str] | None = None,
    auto_staged_paths: list[str] | None = None,
    auto_stage_entry_state: dict[str, dict] | None = None,
) -> tuple[list[dict], dict, list[dict], str]:
    expected = set(expected_paths or [])
    staged = set(staged_paths or [])
    remaining = set(remaining_paths or [])
    auto_staged = set(auto_staged_paths or [])
    auto_stage_state = auto_stage_entry_state or {}
    decisions: list[dict] = []
    remaining_unstaged: list[dict] = []
    summary = {"auto_staged": 0, "ignored": 0, "blocked": 0}
    for entry in entries:
        path = str(entry.get("path") or "")
        if expected and path not in expected and path not in remaining and not is_publish_ignored_change_path(path):
            continue
        decision = {
            "path": path,
            "file_type": entry.get("file_type") or "unknown",
            "classification_source": entry.get("classification_source") or "fallback",
            "publishable": bool(entry.get("publishable")),
            "publish_reason": entry.get("publish_reason") or "",
            "tracked": bool(entry.get("tracked", True)),
            "staged": bool(entry.get("staged")),
            "unstaged": bool(entry.get("unstaged")),
            "untracked": bool(entry.get("untracked")),
            "action": "",
            "reason": "",
        }
        if not decision["publishable"]:
            if decision["file_type"] == "state":
                decision["action"] = "ignored"
                decision["reason"] = decision["publish_reason"] or "explicitly ignored"
                summary["ignored"] += 1
            elif path in remaining:
                decision["action"] = "true_blocker"
                decision["reason"] = "unknown/generated artifact; requires manual review"
                summary["blocked"] += 1
                remaining_unstaged.append(
                    {
                        "path": path,
                        "file_type": decision["file_type"],
                        "classification_source": decision["classification_source"],
                        "publishable": decision["publishable"],
                        "tracked": decision["tracked"],
                        "staged": decision["staged"],
                        "unstaged": decision["unstaged"],
                        "untracked": decision["untracked"],
                        "reason": decision["publish_reason"] or decision["reason"],
                    }
                )
            else:
                decision["action"] = "ignored"
                decision["reason"] = decision["publish_reason"] or "non-publishable file"
                summary["ignored"] += 1
            decisions.append(decision)
            continue
        if path in remaining:
            decision["action"] = "needs_staging"
            decision["reason"] = "publishable file still requires manual staging"
            remaining_unstaged.append(
                {
                    "path": path,
                    "file_type": decision["file_type"],
                    "classification_source": decision["classification_source"],
                    "publishable": decision["publishable"],
                    "tracked": decision["tracked"],
                    "staged": decision["staged"],
                    "unstaged": decision["unstaged"],
                    "untracked": decision["untracked"],
                    "reason": decision["reason"],
                }
            )
        elif path in auto_staged:
            decision["action"] = "auto_staged"
            pre_stage = auto_stage_state.get(path) or {}
            if pre_stage.get("untracked") or not pre_stage.get("tracked", True):
                decision["reason"] = f"safe new publishable {decision['file_type']} file"
            else:
                decision["reason"] = f"safe tracked {decision['file_type']} file"
            summary["auto_staged"] += 1
        elif path in staged:
            decision["action"] = "already_staged"
            decision["reason"] = "publishable file already staged"
        else:
            decision["action"] = "ignored"
            decision["reason"] = "not part of current publish set"
            summary["ignored"] += 1
        decisions.append(decision)
    if summary["blocked"]:
        overall_reason = "one or more files were classified as unknown/artifact and require manual review"
        if any(item.get("reason") == "publishable file still requires manual staging" for item in remaining_unstaged):
            overall_reason = "one or more publishable files still require manual staging"
    elif summary["auto_staged"]:
        overall_reason = "safe publishable files were auto-staged and re-audited successfully"
    elif summary["ignored"] and not expected:
        overall_reason = "only excluded/internal files were detected"
    else:
        overall_reason = "all publishable changes already staged"
    return decisions, summary, remaining_unstaged, overall_reason


def summarize_publish_decision_sets(decisions: list[dict], remaining_unstaged: list[dict] | None = None) -> dict:
    safe_staged_paths = [
        str(item.get("path") or "")
        for item in decisions
        if str(item.get("action") or "") in {"auto_staged", "already_staged"}
    ]
    ignored_nonblocking_paths = [
        str(item.get("path") or "")
        for item in decisions
        if str(item.get("action") or "") == "ignored"
    ]
    safe_stage_candidate_paths = [
        str(item.get("path") or "")
        for item in decisions
        if str(item.get("action") or "") == "needs_staging"
    ]
    unresolved = list(remaining_unstaged or [])
    true_blockers = [
        {
            "path": str(item.get("path") or ""),
            "file_type": str(item.get("file_type") or "unknown"),
            "reason": str(item.get("reason") or ""),
        }
        for item in decisions
        if str(item.get("action") or "") == "true_blocker"
    ]
    return {
        "safe_staged_paths": [path for path in safe_staged_paths if path],
        "ignored_nonblocking_paths": [path for path in ignored_nonblocking_paths if path],
        "safe_stage_candidate_paths": [path for path in safe_stage_candidate_paths if path],
        "true_blockers": true_blockers,
        "blocker_count": len(true_blockers),
        "publishable_ready": not bool(true_blockers) and not bool(safe_stage_candidate_paths),
        "unresolved_paths": [str(item.get("path") or "") for item in unresolved if item.get("path")],
    }


def publish_classification_confidence(entry: dict) -> str:
    source = str(entry.get("classification_source") or "fallback")
    file_type = str(entry.get("file_type") or "unknown")
    if source in {"explicit_ignore", "path_rule", "pattern_match"}:
        return "high"
    if source == "extension" and file_type not in {"unknown"}:
        return "medium"
    return "low"


def recommend_publish_block_action(repo: Path, item: dict) -> dict:
    path = str(item.get("path") or "")
    file_type = str(item.get("file_type") or "unknown")
    publishable = bool(item.get("publishable"))
    tracked = bool(item.get("tracked", True))
    untracked = bool(item.get("untracked"))
    classification_source = str(item.get("classification_source") or "fallback")
    confidence = publish_classification_confidence(item)
    rel_path = Path(path)
    existing_similar = False
    try:
        if rel_path.suffix:
            existing_similar = any(
                candidate != rel_path and candidate.suffix.lower() == rel_path.suffix.lower()
                for candidate in repo.rglob(f"*{rel_path.suffix}")
            )
    except Exception:
        existing_similar = False
    if publishable:
        blocking_reason = "publishable file is still outside the staged publish set"
        recommended_action = "stage and include in publish"
        commands = [f"git add -- {shlex.quote(path)}"]
        if not tracked or untracked:
            reason_suffix = "new publishable file under a repo path that is normally published"
        else:
            reason_suffix = "tracked publishable file changed after the last staging step"
        return {
            "path": path,
            "file_type": file_type,
            "classification_source": classification_source,
            "publishable": publishable,
            "tracked": tracked,
            "staged": bool(item.get("staged")),
            "unstaged": bool(item.get("unstaged")),
            "untracked": untracked,
            "confidence": confidence,
            "blocking_reason": f"{blocking_reason}; {reason_suffix}",
            "recommended_action": recommended_action,
            "recommended_commands": commands,
        }
    if file_type == "state":
        return {
            "path": path,
            "file_type": file_type,
            "classification_source": classification_source,
            "publishable": publishable,
            "tracked": tracked,
            "staged": bool(item.get("staged")),
            "unstaged": bool(item.get("unstaged")),
            "untracked": untracked,
            "confidence": "high",
            "blocking_reason": "internal state file should not drive publish decisions",
            "recommended_action": "leave untracked / do not publish",
            "recommended_commands": [f"git restore --staged -- {shlex.quote(path)}"] if tracked and item.get("staged") else [],
        }
    if file_type in {"artifact", "generated"}:
        ignore_pattern = rel_path.name if rel_path.parent == Path(".") or str(rel_path.parent) == "." else f"{rel_path.parent.as_posix()}/{rel_path.name}"
        commands = []
        if tracked:
            commands.append(f"git restore --staged -- {shlex.quote(path)}")
        if untracked or not tracked:
            commands.append(f"rm {shlex.quote(path)}")
            if existing_similar or rel_path.suffix:
                pattern = f"*{rel_path.suffix}" if rel_path.suffix else ignore_pattern
                commands.append(f"echo {shlex.quote(pattern)} >> .gitignore")
        return {
            "path": path,
            "file_type": file_type,
            "classification_source": classification_source,
            "publishable": publishable,
            "tracked": tracked,
            "staged": bool(item.get("staged")),
            "unstaged": bool(item.get("unstaged")),
            "untracked": untracked,
            "confidence": "high" if file_type == "artifact" else confidence,
            "blocking_reason": "file looks like generated output or a temporary artifact and does not match publishable patterns",
            "recommended_action": "remove generated artifact" if untracked or not tracked else "inspect manually before staging",
            "recommended_commands": commands,
        }
    return {
        "path": path,
        "file_type": file_type,
        "classification_source": classification_source,
        "publishable": publishable,
        "tracked": tracked,
        "staged": bool(item.get("staged")),
        "unstaged": bool(item.get("unstaged")),
        "untracked": untracked,
        "confidence": confidence,
        "blocking_reason": "file does not match a safe publishable pattern and needs operator review",
        "recommended_action": "inspect manually before staging",
        "recommended_commands": [f"git add -- {shlex.quote(path)}", f"git restore --staged -- {shlex.quote(path)}"] if path else [],
    }


def analyze_publish_blockers(repo: Path, entries: list[dict]) -> list[dict]:
    analyses: list[dict] = []
    for entry in entries:
        analyses.append(recommend_publish_block_action(repo, entry))
    return analyses


def classify_publish_blocker_remediation(repo: Path, analysis: dict, policy: dict) -> dict:
    path = str(analysis.get("path") or "")
    file_type = str(analysis.get("file_type") or "unknown")
    confidence = str(analysis.get("confidence") or "low")
    recommended_action = str(analysis.get("recommended_action") or "")
    tracked = bool(analysis.get("tracked", True))
    untracked = bool(analysis.get("untracked"))
    matched_ignore_pattern = publish_path_matches_any_glob(path, list(policy.get("safe_ignore_globs") or []))
    matched_known_junk = publish_path_matches_any_glob(path, list(policy.get("known_junk_globs") or []))
    matched_safe_remove = publish_path_matches_any_glob(path, list(policy.get("safe_remove_globs") or []))
    if recommended_action == "leave untracked / do not publish":
        return {
            "path": path,
            "remediation_class": "policy_resolvable",
            "operation": "nonblocking",
            "reason": "internal or ignored file does not need publish remediation",
            "commands": [],
            "ignore_pattern": "",
        }
    if (
        policy.get("auto_remediate")
        and policy.get("auto_remove_safe_artifacts")
        and confidence == "high"
        and file_type in {"artifact", "generated"}
        and recommended_action == "remove generated artifact"
        and (untracked or not tracked)
        and is_high_confidence_safe_artifact_path(path, analysis, policy)
    ):
        ignore_pattern = matched_ignore_pattern if policy.get("auto_ignore_known_junk") else ""
        if not ignore_pattern and policy.get("auto_ignore_known_junk") and matched_known_junk:
            ignore_pattern = matched_known_junk
        return {
            "path": path,
            "remediation_class": "auto_resolvable_safe",
            "operation": "remove",
            "reason": "high-confidence temporary artifact can be removed safely before publish",
            "commands": [f"rm {shlex.quote(path)}"] + ([f"echo {shlex.quote(ignore_pattern)} >> .gitignore"] if ignore_pattern else []),
            "ignore_pattern": ignore_pattern,
            "matched_policy_pattern": matched_safe_remove or matched_known_junk or "",
        }
    if (
        policy.get("auto_remediate")
        and policy.get("auto_ignore_known_junk")
        and confidence == "high"
        and file_type in {"artifact", "generated"}
        and recommended_action == "remove generated artifact"
        and (untracked or not tracked)
        and (matched_ignore_pattern or matched_known_junk)
    ):
        ignore_pattern = matched_ignore_pattern or matched_known_junk
        return {
            "path": path,
            "remediation_class": "policy_resolvable",
            "operation": "ignore",
            "reason": "high-confidence junk output matches an explicit safe ignore policy",
            "commands": [f"echo {shlex.quote(ignore_pattern)} >> .gitignore"],
            "ignore_pattern": ignore_pattern,
            "matched_policy_pattern": ignore_pattern,
        }
    return {
        "path": path,
        "remediation_class": "ambiguous_requires_manual_review",
        "operation": "manual_review",
        "reason": "blocker is not in an auto-remediable safe class",
        "commands": list(analysis.get("recommended_commands") or []),
        "ignore_pattern": "",
    }


def append_line_if_missing(path: Path, line: str) -> bool:
    existing = ""
    try:
        if path.exists():
            existing = path.read_text()
    except OSError:
        return False
    normalized = line.strip()
    lines = {item.strip() for item in existing.splitlines() if item.strip()}
    if normalized in lines:
        return True
    new_text = existing
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += normalized + "\n"
    try:
        path.write_text(new_text)
    except OSError:
        return False
    return True


def remediate_publish_blockers(repo: Path, analyses: list[dict], policy: dict) -> dict:
    result = {
        "attempted": False,
        "result": "not_needed",
        "auto_removed_paths": [],
        "auto_ignored_patterns": [],
        "resolved_paths": [],
        "remaining_paths": [],
        "details": [],
    }
    if not analyses or not policy.get("auto_remediate"):
        return result
    gitignore_path = repo / ".gitignore"
    partial = False
    for item in analyses:
        remediation = classify_publish_blocker_remediation(repo, item, policy)
        detail = {
            "path": str(item.get("path") or ""),
            "remediation_class": remediation.get("remediation_class", "ambiguous_requires_manual_review"),
            "operation": remediation.get("operation", "manual_review"),
            "reason": remediation.get("reason", ""),
            "applied": False,
        }
        if remediation.get("remediation_class") != "auto_resolvable_safe":
            if remediation.get("remediation_class") == "policy_resolvable" and remediation.get("operation") == "ignore":
                ignore_pattern = str(remediation.get("ignore_pattern") or "")
                if ignore_pattern and append_line_if_missing(gitignore_path, ignore_pattern):
                    result["attempted"] = True
                    detail["applied"] = True
                    result["resolved_paths"].append(detail["path"])
                    if ignore_pattern not in result["auto_ignored_patterns"]:
                        result["auto_ignored_patterns"].append(ignore_pattern)
                    result["details"].append(detail)
                    continue
                partial = True
            result["remaining_paths"].append(detail["path"])
            result["details"].append(detail)
            continue
        target = repo / detail["path"]
        if not target.exists():
            detail["applied"] = True
        else:
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
                else:
                    result["remaining_paths"].append(detail["path"])
                    detail["reason"] = "refused to auto-remove a non-file blocker"
                    result["details"].append(detail)
                    partial = True
                    continue
                detail["applied"] = True
            except OSError as exc:
                result["remaining_paths"].append(detail["path"])
                detail["reason"] = f"failed to remove blocker automatically: {exc}"
                result["details"].append(detail)
                partial = True
                continue
        result["attempted"] = True
        result["auto_removed_paths"].append(detail["path"])
        result["resolved_paths"].append(detail["path"])
        ignore_pattern = str(remediation.get("ignore_pattern") or "")
        if ignore_pattern:
            if append_line_if_missing(gitignore_path, ignore_pattern):
                if ignore_pattern not in result["auto_ignored_patterns"]:
                    result["auto_ignored_patterns"].append(ignore_pattern)
            else:
                partial = True
        result["details"].append(detail)
    if result["attempted"] and not result["remaining_paths"] and not partial:
        result["result"] = "success"
    elif result["attempted"] and result["resolved_paths"]:
        result["result"] = "partial" if result["remaining_paths"] or partial else "success"
    elif result["remaining_paths"]:
        result["result"] = "blocked"
    return result


def summarize_publish_block_categories(analyses: list[dict]) -> dict:
    safe_stage = [item for item in analyses if item.get("recommended_action") == "stage and include in publish"]
    ignored_nonblocking = [item for item in analyses if item.get("recommended_action") == "leave untracked / do not publish"]
    true_blockers = [
        item
        for item in analyses
        if item.get("recommended_action") not in {"stage and include in publish", "leave untracked / do not publish"}
    ]
    return {
        "safe_stage_candidates": safe_stage,
        "ignored_nonblocking": ignored_nonblocking,
        "true_blockers": true_blockers,
    }


def summarize_publish_block_analysis(analyses: list[dict], rerun_command: str = "./scripts/fixpublish.sh") -> dict:
    categories = summarize_publish_block_categories(analyses)
    safe_stage = categories["safe_stage_candidates"]
    ignored_nonblocking = categories["ignored_nonblocking"]
    true_blockers = categories["true_blockers"]
    if not analyses:
        return {
            "blocked_count": 0,
            "blocker_count": 0,
            "safe_stage_candidate_count": 0,
            "ignored_nonblocking_count": 0,
            "safe_staged_paths": [],
            "ignored_nonblocking_paths": [],
            "true_blockers": [],
            "publishable_ready": True,
            "primary_next_step": "",
            "fallback_next_step": "",
            "rerun_command": rerun_command,
        }
    artifact_like = [item for item in true_blockers if item.get("recommended_action") in {"remove generated artifact", "inspect manually before staging"}]
    if safe_stage and not true_blockers:
        primary = "stage the publishable file changes, then rerun publish"
        fallback = "inspect the file manually if you did not intend to publish it"
    elif artifact_like:
        primary = "remove or ignore the artifact-style file, then rerun publish"
        fallback = "inspect the file manually if you intended to keep it in the repo"
    else:
        primary = "inspect the blocking files manually before rerunning publish"
        fallback = "stage only the files you intentionally want to publish"
    return {
        "blocked_count": len(analyses),
        "blocker_count": len(true_blockers),
        "safe_stage_candidate_count": len(safe_stage),
        "ignored_nonblocking_count": len(ignored_nonblocking),
        "safe_staged_paths": [str(item.get("path") or "") for item in safe_stage if item.get("path")],
        "ignored_nonblocking_paths": [str(item.get("path") or "") for item in ignored_nonblocking if item.get("path")],
        "true_blockers": [
            {
                "path": str(item.get("path") or ""),
                "file_type": str(item.get("file_type") or "unknown"),
                "recommended_action": str(item.get("recommended_action") or ""),
            }
            for item in true_blockers
        ],
        "publishable_ready": not bool(true_blockers) and not bool(safe_stage),
        "primary_next_step": primary,
        "fallback_next_step": fallback,
        "rerun_command": rerun_command,
    }


def print_publish_block_analysis(analyses: list[dict], summary: dict) -> None:
    if not analyses:
        return
    categories = summarize_publish_block_categories(analyses)
    safe_stage = categories["safe_stage_candidates"]
    ignored_nonblocking = categories["ignored_nonblocking"]
    true_blockers = categories["true_blockers"]
    print("=== STAGING BLOCK ANALYSIS ===")
    blocker_count = int(summary.get("blocker_count") or len(true_blockers))
    if blocker_count:
        label = "true blocker" if blocker_count == 1 else "true blockers"
        print(f"Publish blocked by {blocker_count} {label}:")
    elif safe_stage:
        label = "safe publishable file" if len(safe_stage) == 1 else "safe publishable files"
        print(f"Publish paused because {len(safe_stage)} {label} still need staging:")
    else:
        print(f"Publish blocked by {int(summary.get('blocked_count') or len(analyses))} unresolved file(s):")
    if summary.get("safe_staged_paths"):
        print(f"safe_staged_paths: {summary.get('safe_staged_paths')}")
    if summary.get("ignored_nonblocking_paths"):
        print(f"ignored_nonblocking_paths: {summary.get('ignored_nonblocking_paths')}")
    if summary.get("true_blockers"):
        print(f"true_blockers: {summary.get('true_blockers')}")
    for item in analyses:
        print(f"- {item.get('path')}")
        print(f"  type: {item.get('file_type')}")
        print(f"  classification_source: {item.get('classification_source')}")
        print(f"  publishable: {format_bool(item.get('publishable'))}")
        print(f"  confidence: {item.get('confidence')}")
        print(f"  reason: {item.get('blocking_reason')}")
        if item.get("remediation_class"):
            print(f"  remediation_class: {item.get('remediation_class')}")
        print(f"  recommended_action: {item.get('recommended_action')}")
        commands = list(item.get("recommended_commands") or [])
        if commands:
            print("  commands:")
            for command in commands:
                print(f"    {command}")
    if summary.get("primary_next_step"):
        print(f"next_step_primary: {summary.get('primary_next_step')}")
    if summary.get("fallback_next_step"):
        print(f"next_step_fallback: {summary.get('fallback_next_step')}")
    if summary.get("rerun_command"):
        print(f"rerun: {summary.get('rerun_command')}")


def normalize_publish_working_tree_audit(
    repo: Path,
    working_tree: dict,
    expected_paths: list[str] | None = None,
    *,
    publish_current_mode: bool = False,
) -> dict:
    expected = set(expected_paths or [])
    fallback_paths = publish_meaningful_changed_paths(repo) if publish_current_mode else meaningful_changed_paths(repo)
    staged_paths = git_cached_staged_paths(repo, list(expected) if expected else None)
    if not staged_paths:
        staged_paths = list(working_tree.get("staged_paths") or [])
    if not staged_paths and working_tree.get("has_staged"):
        staged_paths = list(fallback_paths)
    remaining_paths = sorted(set(working_tree.get("unstaged_paths") or []) | set(working_tree.get("untracked_paths") or []))
    if not remaining_paths and (working_tree.get("has_unstaged") or working_tree.get("has_untracked")):
        remaining_paths = list(fallback_paths)
    if expected:
        staged_paths = [path for path in staged_paths if path in expected]
    entry_status_output = ""
    try:
        entry_status_output = raw_git_status_output(repo)
    except Exception:
        entry_status_output = str(working_tree.get("status_output") or "")
    return {
        "staged_paths": staged_paths,
        "remaining_paths": remaining_paths,
        "entries": collect_publish_working_tree_entries(repo, status_output=entry_status_output),
    }


def is_safe_publish_auto_stage_path(path: str) -> bool:
    return classify_publish_path(path).get("file_type") in {"code", "config", "docs", "script", "test"}


def split_publish_auto_stage_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    safe_paths: list[str] = []
    blocked_paths: list[str] = []
    for path in paths:
        if is_publish_ignored_change_path(path):
            continue
        if is_safe_publish_auto_stage_path(path):
            if path not in safe_paths:
                safe_paths.append(path)
            continue
        if path not in blocked_paths:
            blocked_paths.append(path)
    return safe_paths, blocked_paths


PRE_TASK_AUTO_STAGE_FILE_TYPES = {"code", "docs", "script", "test"}


def classify_pre_task_working_tree(repo: Path, *, auto_remediate: bool = True) -> dict:
    result = {
        "working_tree_detected": False,
        "working_tree_clean": True,
        "working_tree_status": "",
        "dirty_paths": [],
        "auto_stage_attempted": False,
        "auto_staged_paths": [],
        "auto_removed_paths": [],
        "remaining_unstaged_paths": [],
        "working_tree_blockers": [],
        "working_tree_summary": "",
    }
    blocker_policy = load_publish_blocker_policy(repo, auto_remediate=auto_remediate)

    def audit_state() -> tuple[dict, list[dict], list[dict], dict]:
        working_tree = classify_publish_working_tree(repo)
        result["working_tree_clean"] = bool(working_tree.get("clean"))
        result["working_tree_status"] = str(working_tree.get("status_output") or "")
        dirty_paths = list(
            dict.fromkeys(
                list(working_tree.get("staged_paths") or [])
                + list(working_tree.get("unstaged_paths") or [])
                + list(working_tree.get("untracked_paths") or [])
            )
        )
        if dirty_paths and not result["dirty_paths"]:
            result["dirty_paths"] = dirty_paths
        result["working_tree_detected"] = bool(result["working_tree_detected"] or dirty_paths)
        audit = normalize_publish_working_tree_audit(repo, working_tree, expected_paths=None)
        decisions, _summary, remaining_unstaged, _overall_reason = build_publish_file_decisions(
            list(audit.get("entries") or []),
            expected_paths=None,
            staged_paths=list(audit.get("staged_paths") or []),
            remaining_paths=list(audit.get("remaining_paths") or []),
        )
        decision_sets = summarize_publish_decision_sets(decisions, remaining_unstaged)
        analyses = analyze_publish_blockers(repo, remaining_unstaged)
        enriched_analyses: list[dict] = []
        for item in analyses:
            enriched = dict(item)
            enriched.update(classify_publish_blocker_remediation(repo, item, blocker_policy))
            enriched_analyses.append(enriched)
        return working_tree, remaining_unstaged, enriched_analyses, decision_sets

    working_tree, remaining_unstaged, analyses, decision_sets = audit_state()
    if not result["working_tree_detected"]:
        result["working_tree_summary"] = "Working tree already clean."
        return result

    safe_stage_paths = [
        str(item.get("path") or "")
        for item in remaining_unstaged
        if bool(item.get("publishable"))
        and str(item.get("file_type") or "") in PRE_TASK_AUTO_STAGE_FILE_TYPES
        and str(item.get("path") or "")
    ]
    if safe_stage_paths:
        result["auto_stage_attempted"] = True
        code, output = run_subprocess(["git", "add", "-A", "--", *safe_stage_paths], repo)
        if code != 0:
            result["remaining_unstaged_paths"] = list(decision_sets.get("unresolved_paths") or [])
            result["working_tree_blockers"] = [
                {
                    "path": path,
                    "file_type": "unknown",
                    "reason": f"automatic staging failed: {output.strip() or 'git add failed'}",
                }
                for path in safe_stage_paths
            ]
            result["working_tree_summary"] = "Uncommitted changes detected, but automatic staging failed."
            return result
        result["auto_staged_paths"] = sorted(set(safe_stage_paths))
        working_tree, remaining_unstaged, analyses, decision_sets = audit_state()

    remediation_attempts = 0
    while remediation_attempts < 3:
        auto_resolvable = [
            item
            for item in analyses
            if str(item.get("remediation_class") or "") == "auto_resolvable_safe"
        ]
        if not auto_resolvable:
            break
        remediation = remediate_publish_blockers(repo, analyses, blocker_policy)
        remediation_attempts += 1
        result["auto_removed_paths"] = sorted(
            set(list(result.get("auto_removed_paths") or []) + list(remediation.get("auto_removed_paths") or []))
        )
        if str(remediation.get("result") or "") == "blocked":
            break
        working_tree, remaining_unstaged, analyses, decision_sets = audit_state()

    blockers: list[dict] = list(decision_sets.get("true_blockers") or [])
    for item in remaining_unstaged:
        path = str(item.get("path") or "")
        file_type = str(item.get("file_type") or "unknown")
        if not path or not bool(item.get("publishable")):
            continue
        if path in result["auto_staged_paths"]:
            continue
        blockers.append(
            {
                "path": path,
                "file_type": file_type,
                "reason": "publishable file requires manual review before staging",
            }
        )
    deduped_blockers: list[dict] = []
    seen_blockers: set[str] = set()
    for item in blockers:
        path = str(item.get("path") or "")
        if not path or path in seen_blockers:
            continue
        seen_blockers.add(path)
        deduped_blockers.append(item)
    result["working_tree_blockers"] = deduped_blockers
    result["remaining_unstaged_paths"] = list(decision_sets.get("unresolved_paths") or [])
    if result["working_tree_blockers"]:
        blocker_count = len(result["working_tree_blockers"])
        noun = "file" if blocker_count == 1 else "files"
        result["working_tree_summary"] = (
            "Uncommitted changes detected; safe files were staged and artifacts removed where possible."
            if result["auto_staged_paths"] or result["auto_removed_paths"]
            else "Uncommitted changes detected."
        )
        result["working_tree_summary"] += f" Blocked due to {blocker_count} ambiguous {noun} requiring manual review."
        return result
    if result["auto_staged_paths"] or result["auto_removed_paths"]:
        actions: list[str] = []
        if result["auto_staged_paths"]:
            actions.append("staged safe files")
        if result["auto_removed_paths"]:
            actions.append("removed artifacts")
        result["working_tree_summary"] = "Uncommitted changes detected; " + " and ".join(actions) + "."
    else:
        result["working_tree_summary"] = "Uncommitted changes detected, but only ignored/internal files remain."
    return result


def format_manual_staging_commands(paths: list[str], restore_paths: list[str] | None = None) -> list[str]:
    commands: list[str] = []
    for path in paths:
        commands.append(f"git add -- {shlex.quote(path)}")
    restore_candidates = [path for path in (restore_paths or []) if path]
    if restore_candidates:
        commands.append("git restore --staged -- " + " ".join(shlex.quote(path) for path in restore_candidates))
    return commands


def format_manual_staging_handoff(reason: str, paths: list[str], restore_paths: list[str] | None = None) -> str:
    commands = format_manual_staging_commands(paths, restore_paths=restore_paths)
    lines = [reason.strip() or "Manual staging is required."]
    if commands:
        lines.append("Run:")
        lines.extend(commands)
    return "\n".join(lines)


def compute_meaningful_content_fingerprint(repo: Path, publish_changes: dict) -> str:
    meaningful_paths = list(publish_changes.get("meaningful_paths") or [])
    if not meaningful_paths:
        return ""
    status_lines: list[str] = []
    for line in str(publish_changes.get("status_output") or "").splitlines():
        path = extract_status_path(line)
        if path in meaningful_paths:
            status_lines.append(line)
    payload: list[dict[str, str]] = []
    for path in meaningful_paths:
        rel = Path(path)
        file_path = repo / rel
        if file_path.exists() and file_path.is_file():
            try:
                content = file_path.read_bytes()
                content_hash = hashlib.sha256(normalize_meaningful_fingerprint_bytes(content)).hexdigest()
            except OSError:
                content_hash = "unreadable"
        else:
            content_hash = "__missing__"
        payload.append({"path": path, "content_hash": content_hash})
    serialized = json.dumps(
        {
            "paths": meaningful_paths,
            "status_lines": status_lines,
            "files": payload,
        },
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def normalize_meaningful_fingerprint_bytes(content: bytes) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    normalized_lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized_text = "\n".join(normalized_lines)
    return normalized_text.encode("utf-8")


def conflicted_git_paths(repo: Path) -> list[str]:
    if not is_git_repo(repo):
        return []
    code, output = run_subprocess(["git", "diff", "--name-only", "--diff-filter=U"], repo)
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def classify_conflict_file(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return "unknown"
    rel = Path(normalized)
    suffix = rel.suffix.lower()
    if normalized in publish_ignored_change_paths():
        return "state"
    if normalized == DOCS_STATE_FILE_NAME:
        return "state"
    if normalized == "README.md" or normalized.startswith("docs/") or suffix == ".md":
        return "docs"
    if normalized.startswith("tests/") or rel.name.startswith("test_") or rel.name.endswith("_test.py"):
        return "tests"
    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        return "config"
    if suffix in {".py", ".sh", ".js", ".ts", ".tsx", ".jsx"}:
        return "code"
    return "unknown"


def parse_conflict_blocks(text: str) -> tuple[bool, list[dict]]:
    lines = text.splitlines(keepends=True)
    blocks: list[dict] = []
    current_normal: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("<<<<<<< "):
            if current_normal:
                blocks.append({"type": "normal", "text": "".join(current_normal)})
                current_normal = []
            i += 1
            ours: list[str] = []
            while i < len(lines) and not lines[i].startswith("======="):
                ours.append(lines[i])
                i += 1
            if i >= len(lines):
                return False, []
            i += 1
            theirs: list[str] = []
            while i < len(lines) and not lines[i].startswith(">>>>>>> "):
                theirs.append(lines[i])
                i += 1
            if i >= len(lines):
                return False, []
            i += 1
            blocks.append({"type": "conflict", "ours": "".join(ours), "theirs": "".join(theirs)})
            continue
        current_normal.append(line)
        i += 1
    if current_normal:
        blocks.append({"type": "normal", "text": "".join(current_normal)})
    return True, blocks


def summarize_code_or_text_change(text: str, category: str, path: str = "") -> str:
    stripped = str(text or "").strip()
    lowered = stripped.lower()
    if not stripped:
        return "keeps no content in the conflicted block"
    if category == "config":
        keys = re.findall(r'"([^"]+)"\s*:', stripped)
        unique_keys = []
        for key in keys:
            if key not in unique_keys:
                unique_keys.append(key)
        if unique_keys:
            preview = ", ".join(unique_keys[:3])
            return f"changes config keys: {preview}"
        return "changes configuration values"
    if category == "docs":
        added_lines = [line.strip("-* ").strip() for line in stripped.splitlines() if line.strip()]
        if added_lines:
            preview = added_lines[0][:80]
            return f"adds documentation content about {preview}"
        return "changes documentation wording"
    if category == "tests":
        if "assert" in lowered:
            return "adds or changes test assertions"
        if "raises" in lowered:
            return "adds exception validation"
        return "changes test coverage"
    signals = [
        ("retry", "adds retry logic"),
        ("proxy", "adds proxy support"),
        ("timeout", "changes timeout handling"),
        ("logging", "adds logging"),
        ("logger", "adds logging"),
        ("json", "changes response parsing"),
        ("response", "changes response parsing"),
        ("parse", "changes parsing logic"),
        ("session", "changes session handling"),
        ("argparse", "changes CLI argument handling"),
        ("click", "changes CLI command handling"),
        ("typer", "changes CLI command handling"),
    ]
    for token, message in signals:
        if token in lowered:
            return message
    if category == "code":
        if "def " in lowered and "return" in lowered:
            return "refactors function logic"
        return "changes code logic in the conflicted block"
    return f"changes {Path(path).name or 'the conflicted file'}"


def explain_conflict_hunks(path: str, category: str, blocks: list[dict]) -> dict:
    conflict_blocks = [block for block in blocks if block.get("type") == "conflict"]
    explanations: list[dict] = []
    ours_items: list[str] = []
    theirs_items: list[str] = []
    reasons: list[str] = []
    suggestions: list[str] = []
    for index, block in enumerate(blocks):
        if block.get("type") != "conflict":
            continue
        ours = str(block.get("ours") or "")
        theirs = str(block.get("theirs") or "")
        ours_summary = summarize_code_or_text_change(ours, category, path)
        theirs_summary = summarize_code_or_text_change(theirs, category, path)
        if category == "config":
            conflict_reason = "both sides modify the same config block differently"
            suggested_resolution = "merge both only if the resulting config keeps the intended keys and values compatible"
        elif category == "docs":
            conflict_reason = "both sides edit the same documentation section"
            suggested_resolution = "merge both if the guidance is complementary; otherwise prefer the version with the intended wording"
        elif category == "tests":
            conflict_reason = "both sides edit the same test block"
            suggested_resolution = "merge both if the assertions are compatible; otherwise keep the stricter version that still matches the intended behavior"
        elif category == "code":
            conflict_reason = "both sides edit the same code block"
            suggested_resolution = "merge both if the logic changes are compatible; otherwise keep the side that preserves the intended behavior"
        else:
            conflict_reason = "both sides edit the same block and the intent is unclear"
            suggested_resolution = "review both sides manually before marking the file resolved"
        explanations.append(
            {
                "file": path,
                "file_type": category,
                "hunk_index": len(explanations) + 1,
                "ours_summary": ours_summary,
                "theirs_summary": theirs_summary,
                "conflict_reason": conflict_reason,
                "suggested_resolution": suggested_resolution,
            }
        )
        if ours_summary not in ours_items:
            ours_items.append(ours_summary)
        if theirs_summary not in theirs_items:
            theirs_items.append(theirs_summary)
        if conflict_reason not in reasons:
            reasons.append(conflict_reason)
        if suggested_resolution not in suggestions:
            suggestions.append(suggested_resolution)
    return {
        "file": path,
        "file_type": category,
        "hunk_count": len(conflict_blocks),
        "ours_summary": "; ".join(ours_items) if ours_items else "",
        "theirs_summary": "; ".join(theirs_items) if theirs_items else "",
        "conflict_reason": "; ".join(reasons) if reasons else "",
        "suggested_resolution": "; ".join(suggestions) if suggestions else "",
        "hunks": explanations,
    }


def merge_unique_lines(first: str, second: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (first.splitlines(), second.splitlines()):
        for line in raw:
            key = line.rstrip()
            if key in seen and key:
                continue
            if key:
                seen.add(key)
            merged.append(line)
    suffix = "\n" if first.endswith("\n") or second.endswith("\n") else ""
    return "\n".join(merged) + suffix


def score_test_conflict_side(text: str) -> tuple[int, int]:
    lowered = text.lower()
    strictness = lowered.count("assert") + lowered.count("raises") + lowered.count("parametrize")
    return strictness, len([line for line in text.splitlines() if line.strip()])


def text_is_syntax_valid(path: str, content: str) -> bool:
    if Path(path).suffix.lower() != ".py":
        return True
    try:
        ast.parse(content)
    except SyntaxError:
        return False
    return True


def render_conflict_resolution(blocks: list[dict], chooser) -> str:
    rendered: list[str] = []
    for block in blocks:
        if block.get("type") == "normal":
            rendered.append(str(block.get("text") or ""))
            continue
        rendered.append(str(chooser(str(block.get("ours") or ""), str(block.get("theirs") or ""))))
    return "".join(rendered)


def resolve_conflicted_text(path: str, content: str, category: str) -> dict:
    parsed, blocks = parse_conflict_blocks(content)
    if not parsed:
        return {"ok": False, "strategy": "blocked_invalid_markers", "reason": "invalid conflict markers"}
    explanation = explain_conflict_hunks(path, category, blocks)
    if category == "docs":
        resolved = render_conflict_resolution(
            blocks,
            lambda ours, theirs: theirs if len(theirs.strip().splitlines()) >= len(ours.strip().splitlines()) else ours,
        )
        return {"ok": True, "strategy": "prefer_newer_docs_content", "content": resolved, "explanation": explanation}
    if category == "tests":
        resolved = render_conflict_resolution(
            blocks,
            lambda ours, theirs: theirs if score_test_conflict_side(theirs) >= score_test_conflict_side(ours) else ours,
        )
        if not text_is_syntax_valid(path, resolved):
            return {"ok": False, "strategy": "blocked_ambiguous_test_conflict", "reason": "resolved test conflict is not syntactically valid", "explanation": explanation}
        return {"ok": True, "strategy": "prefer_stricter_test_content", "content": resolved, "explanation": explanation}
    if category == "config":
        if len(blocks) == 1 and blocks[0].get("type") == "conflict":
            ours = str(blocks[0].get("ours") or "")
            theirs = str(blocks[0].get("theirs") or "")
            if ours.strip() == theirs.strip():
                return {"ok": True, "strategy": "trivial_config_merge", "content": theirs, "explanation": explanation}
            if not ours.strip():
                return {"ok": True, "strategy": "prefer_nonempty_config", "content": theirs, "explanation": explanation}
            if not theirs.strip():
                return {"ok": True, "strategy": "prefer_nonempty_config", "content": ours, "explanation": explanation}
        return {"ok": False, "strategy": "blocked_ambiguous_config_conflict", "reason": "config conflict is not clearly compatible", "explanation": explanation}
    if category == "unknown":
        return {"ok": False, "strategy": "blocked_unknown_conflict", "reason": "unknown conflicted file type requires manual resolution", "explanation": explanation}
    merged = render_conflict_resolution(blocks, merge_unique_lines)
    if text_is_syntax_valid(path, merged):
        return {"ok": True, "strategy": "structured_merge_combined_logic", "content": merged, "explanation": explanation}
    return {"ok": False, "strategy": "blocked_ambiguous_code_conflict", "reason": "structured code merge remained syntactically invalid", "explanation": explanation}


def latest_repo_validation_command(repo: Path) -> str:
    state = load_recent_state()
    runs = [item for item in state.get("recent_runs", []) if isinstance(item, dict)]
    for item in reversed(runs):
        if item.get("repo") != str(repo) or item.get("target"):
            continue
        command = str(item.get("validation_command") or item.get("test_cmd") or "").strip()
        if command:
            return command
    return ""


def detect_merge_conflicts(repo: Path) -> dict:
    conflicted = conflicted_git_paths(repo)
    return {
        "merge_conflicts_detected": bool(conflicted),
        "conflicted_files": conflicted,
        "conflict_types": {path: classify_conflict_file(path) for path in conflicted},
    }


def resolve_merge_conflicts(repo: Path, validation_command: str = "", no_auto_merge_conflicts: bool = False) -> dict:
    detected = detect_merge_conflicts(repo)
    git_sequence_state = detect_git_sequence_state(repo)
    result = {
        "merge_conflicts_detected": bool(detected.get("merge_conflicts_detected")),
        "conflicted_files": list(detected.get("conflicted_files") or []),
        "resolution_strategy_per_file": {},
        "validation_result_after_merge": "not_run",
        "validation_command": validation_command or "",
        "merge_result": "not_needed" if not detected.get("merge_conflicts_detected") else "blocked",
        "blocked_reason": "",
        "commit_sha": "",
        "sync_operation_attempted": False,
        "sync_operation": "none",
        "conflict_source": git_sequence_state if git_sequence_state != "none" else "none",
        "auto_conflict_resolution_attempted": False,
        "git_sequence_state": git_sequence_state,
        "conflict_explanations": {},
    }
    if not detected.get("merge_conflicts_detected"):
        return result
    if no_auto_merge_conflicts:
        result["validation_result_after_merge"] = "not_run"
        result["blocked_reason"] = "merge conflicts detected and auto-resolution is disabled by --no-auto-merge-conflicts"
        for rel_path in result["conflicted_files"]:
            result["resolution_strategy_per_file"][rel_path] = "strict_mode_blocked"
            result["conflict_explanations"][rel_path] = {
                "file": rel_path,
                "file_type": classify_conflict_file(rel_path),
                "hunk_count": 0,
                "ours_summary": "",
                "theirs_summary": "",
                "conflict_reason": "auto-resolution is disabled for this run",
                "suggested_resolution": manual_merge_hint_for_file(rel_path),
                "hunks": [],
            }
        return result
    result["auto_conflict_resolution_attempted"] = True
    resolved_files: list[str] = []
    for rel_path in result["conflicted_files"]:
        category = classify_conflict_file(rel_path)
        abs_path = safe_repo_path(repo, rel_path)
        if category == "state":
            code, output = run_subprocess(["git", "checkout", "--theirs", "--", rel_path], repo)
            if code != 0:
                result["resolution_strategy_per_file"][rel_path] = "blocked_state_theirs_checkout_failed"
                result["blocked_reason"] = output.strip() or f"failed to resolve state conflict for {rel_path}"
                result["merge_result"] = "blocked"
                return result
            result["resolution_strategy_per_file"][rel_path] = "take_theirs_state_file"
            resolved_files.append(rel_path)
            continue
        try:
            content = abs_path.read_text()
        except OSError as exc:
            result["resolution_strategy_per_file"][rel_path] = "blocked_unreadable"
            result["blocked_reason"] = f"failed to read conflicted file {rel_path}: {exc}"
            result["merge_result"] = "blocked"
            return result
        resolved = resolve_conflicted_text(rel_path, content, category)
        if resolved.get("explanation"):
            result["conflict_explanations"][rel_path] = resolved["explanation"]
        result["resolution_strategy_per_file"][rel_path] = resolved.get("strategy", "blocked")
        if not resolved.get("ok"):
            result["blocked_reason"] = str(resolved.get("reason") or f"ambiguous conflict in {rel_path}")
            result["merge_result"] = "blocked"
            return result
        abs_path.write_text(str(resolved.get("content") or ""))
        resolved_files.append(rel_path)
    if not resolved_files:
        result["blocked_reason"] = "no conflicted files were resolved"
        result["merge_result"] = "blocked"
        return result
    command = validation_command.strip() or latest_repo_validation_command(repo)
    result["validation_command"] = command
    if command:
        code, output = run_subprocess(command, repo, shell=True)
        result["validation_result_after_merge"] = "success" if code == 0 else "failed"
        if code != 0:
            result["blocked_reason"] = (output or "").strip()[:500] or "validation failed after merge resolution"
            result["merge_result"] = "blocked"
            return result
    else:
        result["validation_result_after_merge"] = "blocked"
        result["blocked_reason"] = "no validation command available after merge resolution"
        result["merge_result"] = "blocked"
        return result
    add_code, add_output = run_subprocess(["git", "add", "--"] + resolved_files, repo)
    if add_code != 0:
        result["validation_result_after_merge"] = "blocked"
        result["blocked_reason"] = add_output.strip() or "failed to stage resolved files"
        result["merge_result"] = "blocked"
        return result
    sequence_state = detect_git_sequence_state(repo)
    if sequence_state == "merge":
        commit_code, commit_output = run_subprocess(["git", "commit", "-m", "auto-resolved merge conflicts with validation"], repo)
    elif sequence_state == "rebase":
        commit_code, commit_output = run_subprocess(["git", "rebase", "--continue"], repo)
    elif sequence_state == "cherry_pick":
        commit_code, commit_output = run_subprocess(["git", "cherry-pick", "--continue"], repo)
    else:
        commit_code, commit_output = run_subprocess(["git", "commit", "-m", "auto-resolved merge conflicts with validation"], repo)
    if commit_code != 0:
        result["validation_result_after_merge"] = "blocked"
        result["blocked_reason"] = commit_output.strip() or f"failed to continue {sequence_state or 'merge'} after conflict resolution"
        result["merge_result"] = "blocked"
        return result
    result["merge_result"] = "success"
    result["commit_sha"] = parse_head_commit(repo)
    return result


def print_conflict_explanations(result: dict) -> None:
    explanations = dict(result.get("conflict_explanations") or {})
    if not explanations:
        return
    print("\n=== CONFLICT EXPLANATION ===")
    for rel_path in result.get("conflicted_files") or []:
        item = explanations.get(rel_path) or {}
        print(f"file: {rel_path}")
        print(f"file_type: {item.get('file_type') or classify_conflict_file(rel_path)}")
        print(f"hunk_count: {int(item.get('hunk_count', 0) or 0)}")
        print(f"ours_summary: {item.get('ours_summary') or '(none)'}")
        print(f"theirs_summary: {item.get('theirs_summary') or '(none)'}")
        print(f"conflict_reason: {item.get('conflict_reason') or '(none)'}")
        print(f"suggested_resolution: {item.get('suggested_resolution') or manual_merge_hint_for_file(rel_path)}")


def print_merge_conflict_summary(result: dict) -> None:
    print("\n=== MERGE CONFLICTS ===")
    print(f"sync_operation_attempted: {format_bool(result.get('sync_operation_attempted'))}")
    print(f"sync_operation: {result.get('sync_operation') or 'none'}")
    print(f"merge_conflicts_detected: {format_bool(result.get('merge_conflicts_detected'))}")
    print(f"conflict_source: {result.get('conflict_source') or 'none'}")
    print(f"auto_conflict_resolution_attempted: {format_bool(result.get('auto_conflict_resolution_attempted'))}")
    print(f"conflicted_files: {result.get('conflicted_files') or []}")
    print(f"resolution_strategy_per_file: {result.get('resolution_strategy_per_file') or {}}")
    print(f"validation_result_after_merge: {result.get('validation_result_after_merge') or 'not_needed'}")
    print(f"merge_result: {result.get('merge_result') or 'blocked'}")
    if result.get("blocked_reason"):
        print(f"merge_blocked_reason: {result['blocked_reason']}")
    if result.get("commit_sha"):
        print(f"merge_commit: {result.get('commit_sha')}")
    print_conflict_explanations(result)
    if result.get("merge_conflicts_detected") and result.get("merge_result") == "blocked":
        print_manual_merge_required(result)


def manual_merge_reason_for_file(path: str, strategy: str, blocked_reason: str) -> str:
    category = classify_conflict_file(path)
    if strategy == "blocked_ambiguous_config_conflict":
        return "ambiguous config conflict; both sides modify the same settings differently"
    if strategy == "blocked_ambiguous_code_conflict":
        return "ambiguous code conflict; structured merge could not preserve both logic paths safely"
    if strategy == "blocked_ambiguous_test_conflict":
        return "ambiguous test conflict; auto-merged test content did not stay syntactically valid"
    if strategy == "blocked_unknown_conflict":
        return "unknown conflict type; the file needs manual review before continuing"
    if strategy == "blocked_invalid_markers":
        return "invalid conflict markers; Git conflict blocks could not be parsed safely"
    if strategy == "blocked_unreadable":
        return blocked_reason or "the conflicted file could not be read safely"
    if strategy == "strict_mode_blocked":
        if category == "config":
            return "ambiguous config conflict; auto-resolution is disabled and both sides need manual review"
        if category == "docs":
            return "docs conflict requires manual review because auto-resolution is disabled"
        if category == "tests":
            return "test conflict requires manual review because auto-resolution is disabled"
        if category == "code":
            return "code conflict requires manual review because auto-resolution is disabled"
        return "conflict requires manual review because auto-resolution is disabled"
    if blocked_reason:
        return blocked_reason
    if category == "docs":
        return "docs conflict was not safe to resolve automatically"
    if category == "tests":
        return "test conflict was not safe to resolve automatically"
    if category == "code":
        return "code conflict was not safe to resolve automatically"
    if category == "config":
        return "config conflict was not safe to resolve automatically"
    return "conflict was not safe to resolve automatically"


def manual_merge_hint_for_file(path: str) -> str:
    category = classify_conflict_file(path)
    if category == "config":
        return "use ours if local settings are known-good; use theirs if upstream defaults must win; merge both only if the combined config remains compatible"
    if category == "docs":
        return "use ours if local docs describe the intended behavior; use theirs if upstream wording is authoritative; merge both if each side adds complementary guidance"
    if category == "tests":
        return "use ours if local tests cover the correct behavior; use theirs if upstream adds required assertions; merge both if each side adds valid coverage"
    if category == "code":
        return "use ours if local logic is known-good; use theirs if the upstream fix should win; merge both if each side adds valid complementary logic"
    if category == "state":
        return "prefer the side that matches the current repo state, or regenerate the state file if it is machine-local"
    return "use ours if local changes should win; use theirs if upstream is authoritative; merge both only when the changes are clearly complementary"


def resume_agent_command(argv: Sequence[str] | None = None) -> str:
    args = list(argv if argv is not None else sys.argv[1:])
    return f"python local_fix_agent.py {shlex.join(args)}".strip()


def print_manual_merge_required(result: dict, argv: Sequence[str] | None = None) -> None:
    conflicted_files = list(result.get("conflicted_files") or [])
    blocked_reason = str(result.get("blocked_reason") or "manual merge resolution is required")
    sequence_state = str(result.get("git_sequence_state") or "none")
    strategy_per_file = dict(result.get("resolution_strategy_per_file") or {})
    explanations = dict(result.get("conflict_explanations") or {})
    print("\n=== MANUAL MERGE REQUIRED ===")
    print(f"conflicted_files: {conflicted_files}")
    print(f"reason_for_block: {blocked_reason}")
    for rel_path in conflicted_files:
        conflict_type = classify_conflict_file(rel_path)
        strategy = str(strategy_per_file.get(rel_path) or "blocked")
        file_reason = manual_merge_reason_for_file(rel_path, strategy, blocked_reason)
        file_hint = manual_merge_hint_for_file(rel_path)
        explanation = explanations.get(rel_path) or {}
        print(f"file: {rel_path}")
        print(f"file_type: {conflict_type}")
        if explanation.get("hunk_count") is not None:
            print(f"hunk_count: {int(explanation.get('hunk_count', 0) or 0)}")
        if explanation.get("ours_summary"):
            print(f"ours_summary: {explanation.get('ours_summary')}")
        if explanation.get("theirs_summary"):
            print(f"theirs_summary: {explanation.get('theirs_summary')}")
        if explanation.get("conflict_reason"):
            print(f"conflict_reason: {explanation.get('conflict_reason')}")
        if explanation.get("suggested_resolution"):
            print(f"suggested_resolution: {explanation.get('suggested_resolution')}")
        print(f"reason: {file_reason}")
        print(f"hint: {file_hint}")
    print("list_conflicted_files: git diff --name-only --diff-filter=U")
    for rel_path in conflicted_files:
        quoted_path = shlex.quote(rel_path)
        print(f"open_file: <editor> {quoted_path}")
        print(f"accept_ours: git checkout --ours -- {quoted_path}")
        print(f"accept_theirs: git checkout --theirs -- {quoted_path}")
        print(f"mark_resolved: git add {quoted_path}")
    if sequence_state == "rebase":
        print("complete_merge: git rebase --continue")
    elif sequence_state == "cherry_pick":
        print("complete_merge: git cherry-pick --continue")
    else:
        print("complete_merge: git commit")
    print("Resume:")
    print(resume_agent_command(argv))


def maybe_handle_merge_conflicts(
    repo: Path,
    *,
    validation_command: str,
    sync_operation: str = "none",
    publish_requested: bool,
    publish_mode: str,
    publish_branch: str,
    publish_pr: bool,
    publish_merge: bool,
    publish_merge_local_main: bool,
    publish_message: str,
    target: str,
    dry_run_mode: bool,
    force_publish: bool,
    auto_stage_safe_paths: bool = True,
    auto_remediate_blockers: bool = True,
    explain_staging: bool = False,
    no_auto_merge_conflicts: bool = False,
) -> dict | None:
    merge_result = resolve_merge_conflicts(repo, validation_command=validation_command, no_auto_merge_conflicts=no_auto_merge_conflicts)
    merge_result["sync_operation_attempted"] = sync_operation != "none"
    merge_result["sync_operation"] = sync_operation
    if sync_operation != "none":
        merge_result["conflict_source"] = sync_operation
    print_merge_conflict_summary(merge_result)
    if not merge_result.get("merge_conflicts_detected"):
        return None
    if merge_result.get("merge_result") != "success":
        return {
            "handled": True,
            "success": False,
            "merge_result": merge_result,
            "continue_with_repair": False,
        }
    update_recent_state(
        repo,
        validation_command,
        "merge-conflict-resolve",
        "success",
        None,
        target,
        files_changed=list(merge_result.get("conflicted_files") or []),
        confidence="merge-conflict-resolve",
        blocked_reason="",
    )
    if publish_requested:
        publish_summary = run_post_success_publish(
            repo,
            validation_command,
            0,
            "merge-conflict-resolve",
            None,
            list(merge_result.get("conflicted_files") or []),
            publish_branch,
            publish_pr,
            publish_merge,
            publish_merge_local_main,
            publish_message,
            target,
            None,
            [],
            dry_run_mode,
            publish_mode,
            True,
            True,
            force_publish,
            auto_stage_safe_paths,
            auto_remediate_blockers,
            explain_staging,
        )
        print(format_final_operator_summary(publish_summary))
    else:
        print("FINAL: merge conflicts resolved, validated, and committed")
    return {
        "handled": True,
        "success": True,
        "merge_result": merge_result,
        "continue_with_repair": False,
    }


def run_sync_operation_with_conflict_hook(
    repo: Path,
    *,
    sync_operation: str,
    command: list[str],
    validation_command: str = "",
    no_auto_conflict_resolution_after_sync: bool = False,
) -> tuple[bool, str, dict]:
    code, output = run_subprocess(command, repo)
    if code != 0 and not conflicted_git_paths(repo):
        return False, output.strip() or f"{sync_operation} failed", {
            "sync_operation_attempted": True,
            "sync_operation": sync_operation,
            "merge_conflicts_detected": False,
            "conflict_source": sync_operation,
            "auto_conflict_resolution_attempted": False,
            "merge_result": "not_needed",
        }
    conflict_result = resolve_merge_conflicts(
        repo,
        validation_command=validation_command,
        no_auto_merge_conflicts=no_auto_conflict_resolution_after_sync,
    )
    conflict_result["sync_operation_attempted"] = True
    conflict_result["sync_operation"] = sync_operation
    conflict_result["conflict_source"] = sync_operation if conflict_result.get("merge_conflicts_detected") else "none"
    if conflict_result.get("merge_conflicts_detected"):
        return conflict_result.get("merge_result") == "success", conflict_result.get("blocked_reason", ""), conflict_result
    return True, output.strip(), conflict_result


def append_git_action(result: dict, action: str) -> None:
    text = str(action or "").strip()
    if not text:
        return
    result.setdefault("git_actions", []).append(text)
    progress(f"git: {text}")


def detect_remote_default_branch(repo: Path, remote_name: str, fallback_branch: str = "main") -> str:
    code, output = run_subprocess(["git", "symbolic-ref", f"refs/remotes/{remote_name}/HEAD"], repo)
    if code == 0 and output.strip():
        remote_ref = output.strip()
        if remote_ref.startswith("refs/remotes/"):
            return remote_ref[len("refs/remotes/") :]
        return remote_ref
    for candidate in [fallback_branch, "master"]:
        remote_ref = f"{remote_name}/{candidate}"
        code, _ = run_subprocess(["git", "rev-parse", "--verify", remote_ref], repo)
        if code == 0:
            return remote_ref
    return f"{remote_name}/{fallback_branch}"


def git_ref_exists(repo: Path, ref_name: str) -> bool:
    code, _ = run_subprocess(["git", "rev-parse", "--verify", ref_name], repo)
    return code == 0


def parse_ahead_behind_counts(output: str) -> tuple[int, int]:
    parts = (output or "").strip().split()
    if len(parts) < 2:
        return 0, 0
    try:
        ahead = int(parts[0])
        behind = int(parts[1])
    except (TypeError, ValueError):
        return 0, 0
    return ahead, behind


def classify_upstream_change_path(path: str) -> str:
    normalized = (path or "").strip().lower()
    if not normalized:
        return "unknown"
    name = Path(normalized).name
    if normalized == "readme.md" or normalized.startswith("docs/") or name.endswith(".md"):
        return "docs"
    if normalized.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py"):
        return "tests"
    if name in {"pyproject.toml", "requirements.txt", "requirements-dev.txt", "poetry.lock", "uv.lock", "pdm.lock"}:
        return "dependencies"
    if name.endswith((".ini", ".yaml", ".yml", ".json", ".toml", ".cfg")):
        return "config"
    if normalized.startswith("agent/") or normalized.startswith("src/") or name.endswith(".py"):
        return "core_code"
    return "unknown"


def parse_name_status_output(output: str) -> list[dict]:
    changes: list[dict] = []
    for line in (output or "").splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip().upper()
        change_code = status[:1]
        path = parts[-1].strip()
        if not path:
            continue
        change_type = {
            "A": "added",
            "M": "modified",
            "D": "deleted",
            "R": "modified",
            "C": "modified",
        }.get(change_code, "modified")
        changes.append(
            {
                "path": path,
                "change_type": change_type,
                "category": classify_upstream_change_path(path),
            }
        )
    return changes


def semantic_hint_for_upstream_path(path: str, category: str) -> str:
    normalized = (path or "").strip().lower()
    if "langsmith" in normalized:
        return "LangSmith logging or tracing integration changed"
    if "cli" in normalized:
        return "CLI utilities changed"
    if "proxy" in normalized or "network" in normalized:
        return "proxy or network behavior changed"
    if category == "dependencies":
        return "dependency versions or lockfiles changed"
    if category == "docs":
        return "documentation changed"
    if category == "tests":
        return "test coverage changed"
    if category == "config":
        return "configuration defaults changed"
    if category == "core_code":
        return "core agent logic changed"
    return "project files changed"


def summarize_upstream_semantics(changes: list[dict]) -> list[str]:
    categories = {str(item.get("category") or "") for item in changes}
    change_types = {str(item.get("change_type") or "") for item in changes}
    summaries: list[str] = []
    seen = set()
    if categories == {"docs"}:
        return ["docs-only changes"]
    if categories == {"tests"}:
        return ["tests-only changes"]
    for item in changes:
        hint = semantic_hint_for_upstream_path(str(item.get("path") or ""), str(item.get("category") or ""))
        if hint not in seen:
            summaries.append(hint)
            seen.add(hint)
        if len(summaries) >= 3:
            break
    if "added" in change_types and "new files were added" not in seen:
        summaries.append("new files were added")
    if "deleted" in change_types and "files were removed" not in seen:
        summaries.append("files were removed")
    return summaries[:4]


def assess_upstream_change_risk(changes: list[dict], commit_count: int, diff_text: str) -> tuple[str, str]:
    if not changes:
        return "low", "no incoming upstream file changes were detected"
    categories = {str(item.get("category") or "") for item in changes}
    change_types = {str(item.get("change_type") or "") for item in changes}
    diff_lines = len([line for line in (diff_text or "").splitlines() if line.strip()])
    if categories == {"docs"}:
        return "low", "incoming changes are limited to documentation"
    if categories == {"tests"}:
        return "low", "incoming changes are limited to tests"
    if "dependencies" in categories:
        return "high", "dependency or lockfile changes can alter runtime behavior"
    if change_types == {"added"} and "config" not in categories and "dependencies" not in categories:
        return "medium", "incoming upstream changes add new project files"
    if "core_code" in categories:
        return "high", "core code changes can alter agent behavior directly"
    if diff_lines > 400 or len(changes) > 15 or commit_count > 8:
        return "high", "incoming upstream diff is large enough to require explicit review"
    if "added" in change_types or "deleted" in change_types:
        return "medium", "incoming upstream changes add or remove project files"
    return "medium", "incoming upstream changes are broader than docs/tests but do not touch the highest-risk paths"


def analyze_upstream_changes(repo: Path, upstream_branch: str) -> dict:
    log_code, log_output = run_subprocess(["git", "log", f"HEAD..{upstream_branch}", "--oneline"], repo)
    if log_code != 0:
        return {
            "ok": False,
            "reason": log_output.strip() or f"failed to inspect commits for {upstream_branch}",
            "commit_count": 0,
            "changed_files": [],
            "change_types": {},
            "categories": {},
            "summary": "",
            "semantic_summary": "",
            "risk_level": "high",
            "risk_reason": "could not inspect upstream commits safely",
        }
    name_status_code, name_status_output = run_subprocess(["git", "diff", "--name-status", f"HEAD..{upstream_branch}"], repo)
    if name_status_code != 0:
        return {
            "ok": False,
            "reason": name_status_output.strip() or f"failed to inspect changed files for {upstream_branch}",
            "commit_count": 0,
            "changed_files": [],
            "change_types": {},
            "categories": {},
            "summary": "",
            "semantic_summary": "",
            "risk_level": "high",
            "risk_reason": "could not inspect upstream changed files safely",
        }
    diff_code, diff_output = run_subprocess(["git", "diff", f"HEAD..{upstream_branch}"], repo)
    if diff_code != 0:
        return {
            "ok": False,
            "reason": diff_output.strip() or f"failed to inspect upstream diff for {upstream_branch}",
            "commit_count": 0,
            "changed_files": [],
            "change_types": {},
            "categories": {},
            "summary": "",
            "semantic_summary": "",
            "risk_level": "high",
            "risk_reason": "could not inspect the upstream diff safely",
        }
    commits = [line.strip() for line in log_output.splitlines() if line.strip()]
    changes = parse_name_status_output(name_status_output)
    summary_parts = [f"{len(changes)} files changed"]
    core = [item["path"] for item in changes if item.get("category") == "core_code"]
    added = [item["path"] for item in changes if item.get("change_type") == "added"]
    deps = [item["path"] for item in changes if item.get("category") == "dependencies"]
    docs = [item["path"] for item in changes if item.get("category") == "docs"]
    if core:
        summary_parts.append(f"core logic updated in {', '.join(core[:2])}")
    if added:
        summary_parts.append(f"new file added: {', '.join(added[:2])}")
    if deps:
        summary_parts.append(f"dependencies updated: {', '.join(deps[:2])}")
    if docs:
        summary_parts.append(f"docs updated: {', '.join(docs[:2])}")
    semantic_items = summarize_upstream_semantics(changes)
    risk_level, risk_reason = assess_upstream_change_risk(changes, len(commits), diff_output)
    return {
        "ok": True,
        "reason": "",
        "commit_count": len(commits),
        "commits": commits,
        "changed_files": [item["path"] for item in changes],
        "change_types": {item["path"]: item["change_type"] for item in changes},
        "categories": {item["path"]: item["category"] for item in changes},
        "summary": "; ".join(summary_parts[:4]),
        "semantic_summary": "; ".join(semantic_items) if semantic_items else "upstream changes affect tracked project files",
        "risk_level": risk_level,
        "risk_reason": risk_reason,
    }


def run_repo_validation_command(
    repo: Path,
    validation_command: str,
    *,
    mode: str,
    confidence: str,
    target: str = "",
    files_changed: list[str] | None = None,
) -> dict:
    command = validation_command.strip()
    if not command:
        return {
            "ok": False,
            "validation_result": "blocked",
            "reason": f"no validation command available after {mode}",
            "output": "",
            "validation_error_type": "unknown",
            "fallback_validation_used": False,
            "fallback_validation_result": "not_needed",
        }
    code, output = run_subprocess(command, repo, shell=True)
    validation_result = "success" if code == 0 else "failed"
    blocked_reason = ""
    validation_error_type = "unknown"
    fallback_validation_used = False
    fallback_validation_result = "not_needed"
    command_used = command
    if code != 0:
        blocked_reason = (output or "").strip()[:2000]
        validation_error_type = map_validation_error_type(classify_failure_type(output), extract_failure_context(output, repo), output)
        if is_invalid_validation_command_output(output):
            validation_error_type = "invalid_validation_command"
            fallback_validation_used = True
            fallback = run_validation_fallback_chain(repo, failed_command=command, files_changed=files_changed)
            fallback_validation_result = "passed" if fallback.get("ok") else "failed"
            command_used = str(fallback.get("command") or command)
            if fallback.get("ok"):
                validation_result = "success"
                blocked_reason = str(fallback.get("summary") or "Validation command failed; used fallback validation.")
                output = "\n".join(
                    part for part in [str(output or "").strip(), str(fallback.get("summary") or "").strip()] if part
                ).strip()
            else:
                blocked_reason = str(fallback.get("summary") or "Fallback validation failed.")
                if fallback.get("output"):
                    blocked_reason = f"{blocked_reason}: {str(fallback.get('output') or '').strip()[:500]}"
                output = "\n".join(
                    part for part in [str(output or "").strip(), str(fallback.get("output") or "").strip()] if part
                ).strip()
                validation_error_type = str(fallback.get("validation_error_type") or "unknown")
    update_recent_state(
        repo,
        command_used,
        mode,
        validation_result,
        None,
        target,
        files_changed=list(files_changed or []),
        confidence=confidence,
        blocked_reason=blocked_reason,
    )
    return {
        "ok": validation_result == "success",
        "validation_result": validation_result,
        "reason": blocked_reason or "",
        "output": output,
        "validation_error_type": validation_error_type,
        "fallback_validation_used": fallback_validation_used,
        "fallback_validation_result": fallback_validation_result,
        "validation_command_used": command_used,
    }


def make_upstream_sync_result() -> dict:
    return {
        "current_branch": "",
        "working_tree_detected": False,
        "working_tree_clean": True,
        "working_tree_status": "",
        "dirty_paths": [],
        "auto_stage_attempted": False,
        "auto_staged_paths": [],
        "auto_removed_paths": [],
        "remaining_unstaged_paths": [],
        "working_tree_blockers": [],
        "working_tree_summary": "",
        "git_actions": [],
        "origin_detected": False,
        "origin_branch": "",
        "origin_ahead_count": 0,
        "origin_behind_count": 0,
        "origin_sync_attempted": False,
        "origin_sync_result": "not_needed",
        "origin_reason": "",
        "upstream_detected": False,
        "upstream_branch": "upstream/main",
        "behind_count": 0,
        "ahead_count": 0,
        "sync_attempted": False,
        "sync_result": "not_needed",
        "reason": "",
        "merge_conflict_result": None,
        "validation_result_after_sync": "not_run",
        "analysis": {
            "commit_count": 0,
            "changed_files": [],
            "change_types": {},
            "categories": {},
            "summary": "",
            "semantic_summary": "",
            "risk_level": "low",
            "risk_reason": "",
        },
    }


def is_invalid_validation_command_output(output: str) -> bool:
    normalized = normalize_failure_output(output)
    if not normalized:
        return False
    shell_markers = [
        "/bin/sh:",
        "command not found",
        "syntax error",
        "word unexpected",
        "unexpected token",
        "not found",
    ]
    if not any(marker in normalized for marker in shell_markers):
        return False
    real_code_markers = [
        "traceback",
        "syntaxerror",
        "indentationerror",
        "taberror",
        "importerror",
        "modulenotfounderror",
        "assertionerror",
        "error collecting",
        "failed tests",
        "e       assert",
        "nameerror",
        "attributeerror",
    ]
    return not any(marker in normalized for marker in real_code_markers)


def fallback_validation_python_files(repo: Path, files_changed: list[str] | None = None) -> list[str]:
    candidates = [path for path in list(files_changed or []) if path.endswith(".py")]
    if not candidates:
        candidates = [path for path in meaningful_changed_paths(repo) if path.endswith(".py")]
    if not candidates:
        candidates = [path for path in repo_files(repo) if path.endswith(".py")]
    existing: list[str] = []
    for path in candidates:
        try:
            if (repo / path).exists():
                existing.append(path)
        except OSError:
            continue
    return existing[:200]


def repo_has_pytest_tests(repo: Path) -> bool:
    try:
        if (repo / "tests").is_dir():
            return True
    except OSError:
        return False
    return any(path.startswith("tests/") or Path(path).name.startswith("test_") for path in repo_files(repo))


def run_fallback_python_validation(repo: Path, *, files_changed: list[str] | None = None) -> dict:
    py_paths = fallback_validation_python_files(repo, files_changed=files_changed)
    if not py_paths:
        return {
            "ok": True,
            "command": "",
            "files": [],
            "output": "",
            "summary": "Validation command failed; fallback syntax validation found no Python files to check.",
        }
    command = [sys.executable, "-m", "py_compile", *py_paths]
    code, output = run_subprocess(command, repo)
    return {
        "ok": code == 0,
        "command": " ".join(shlex.quote(part) for part in command),
        "files": py_paths,
        "output": output,
        "validation_error_type": map_validation_error_type(classify_failure_type(output), extract_failure_context(output, repo), output) if code != 0 else "unknown",
        "summary": (
            "Validation command failed; used fallback syntax validation."
            if code == 0
            else "Fallback syntax validation failed."
        ),
    }


def run_validation_fallback_chain(
    repo: Path,
    *,
    failed_command: str,
    files_changed: list[str] | None = None,
) -> dict:
    candidates: list[dict] = []
    remembered = latest_repo_validation_command(repo).strip()
    if remembered and remembered != failed_command and not remembered.startswith("n/a ("):
        candidates.append({"kind": "repo_defined_validation", "command": remembered, "shell": True})
    if repo_has_pytest_tests(repo):
        candidates.append({"kind": "pytest", "command": "pytest -q", "shell": True})
    candidates.append({"kind": "py_compile", "command": "", "shell": False})

    attempted: list[dict] = []
    for candidate in candidates:
        if candidate["kind"] == "py_compile":
            fallback = run_fallback_python_validation(repo, files_changed=files_changed)
            attempted.append(
                {
                    "kind": "py_compile",
                    "command": str(fallback.get("command") or ""),
                    "result": "passed" if fallback.get("ok") else "failed",
                    "invalid_or_unavailable": False,
                }
            )
            return {
                "ok": bool(fallback.get("ok")),
                "command": str(fallback.get("command") or ""),
                "kind": "py_compile",
                "output": str(fallback.get("output") or ""),
                "summary": str(fallback.get("summary") or ""),
                "attempted": attempted,
                "validation_error_type": str(fallback.get("validation_error_type") or ("unknown" if fallback.get("ok") else "syntax")),
            }

        code, output = run_subprocess(str(candidate["command"]), repo, shell=bool(candidate["shell"]))
        invalid = is_invalid_validation_command_output(output) or not str(candidate["command"]).strip()
        attempted.append(
            {
                "kind": str(candidate["kind"]),
                "command": str(candidate["command"]),
                "result": "passed" if code == 0 else ("invalid" if invalid else "failed"),
                "invalid_or_unavailable": invalid,
            }
        )
        if code == 0:
            return {
                "ok": True,
                "command": str(candidate["command"]),
                "kind": str(candidate["kind"]),
                "output": output,
                "summary": f"Validation command failed; used fallback {candidate['kind']} validation.",
                "attempted": attempted,
                "validation_error_type": "unknown",
            }
        if not invalid:
            return {
                "ok": False,
                "command": str(candidate["command"]),
                "kind": str(candidate["kind"]),
                "output": output,
                "summary": f"Fallback validation failed at {candidate['kind']}.",
                "attempted": attempted,
                "validation_error_type": map_validation_error_type(classify_failure_type(output), extract_failure_context(output, repo), output),
            }

    return {
        "ok": False,
        "command": "",
        "kind": "none",
        "output": "",
        "summary": "No fallback validation strategy was available.",
        "attempted": attempted,
        "validation_error_type": "unknown",
    }


def print_upstream_change_analysis(analysis: dict) -> None:
    print("\n=== UPSTREAM CHANGE ANALYSIS ===")
    print(f"commits: {int(analysis.get('commit_count', 0) or 0)}")
    print(f"files_changed: {len(analysis.get('changed_files') or [])}")
    print(f"summary: {analysis.get('summary') or '(none)'}")
    print(f"semantic_summary: {analysis.get('semantic_summary') or '(none)'}")
    print(f"risk_level: {analysis.get('risk_level') or 'low'}")
    print(f"risk_reason: {analysis.get('risk_reason') or '(none)'}")


def print_upstream_sync_summary(result: dict) -> None:
    print("\n=== PRE-TASK GIT CHECK ===")
    print(f"current_branch: {result.get('current_branch') or '(unknown)'}")
    print(f"working_tree_detected: {format_bool(result.get('working_tree_detected'))}")
    print(f"working_tree_clean: {format_bool(result.get('working_tree_clean', True))}")
    print(f"auto_stage_attempted: {format_bool(result.get('auto_stage_attempted'))}")
    print(f"auto_staged_paths: {result.get('auto_staged_paths') or []}")
    print(f"auto_removed_paths: {result.get('auto_removed_paths') or []}")
    print(f"remaining_unstaged_paths: {result.get('remaining_unstaged_paths') or []}")
    print(f"working_tree_blockers: {result.get('working_tree_blockers') or []}")
    if result.get("working_tree_summary"):
        print(f"working_tree_summary: {result.get('working_tree_summary')}")
    print(f"origin_detected: {format_bool(result.get('origin_detected'))}")
    print(f"origin_branch: {result.get('origin_branch') or '(none)'}")
    print(f"origin_ahead_count: {int(result.get('origin_ahead_count', 0) or 0)}")
    print(f"origin_behind_count: {int(result.get('origin_behind_count', 0) or 0)}")
    print(f"origin_sync_attempted: {format_bool(result.get('origin_sync_attempted'))}")
    print(f"origin_sync_result: {result.get('origin_sync_result') or 'not_needed'}")
    if result.get("origin_reason"):
        print(f"origin_reason: {result.get('origin_reason')}")
    print(f"upstream_detected: {format_bool(result.get('upstream_detected'))}")
    print(f"upstream_branch: {result.get('upstream_branch') or 'upstream/main'}")
    print(f"behind_count: {int(result.get('behind_count', 0) or 0)}")
    print(f"ahead_count: {int(result.get('ahead_count', 0) or 0)}")
    print(f"sync_attempted: {format_bool(result.get('sync_attempted'))}")
    print(f"sync_result: {result.get('sync_result') or 'not_needed'}")
    if result.get("reason"):
        print(f"sync_reason: {result.get('reason')}")
    actions = list(result.get("git_actions") or [])
    if actions:
        print("git_actions:")
        for action in actions:
            print(f"- {action}")


def sync_with_upstream_before_workflow(
    repo: Path,
    *,
    validation_command: str = "",
    target: str = "",
    no_auto_conflict_resolution_after_sync: bool = False,
    no_upstream_sync: bool = False,
    force_upstream_merge: bool = False,
) -> dict:
    result = make_upstream_sync_result()
    if no_upstream_sync or not is_git_repo(repo):
        return result
    result["current_branch"] = current_git_branch(repo)
    if not result["current_branch"]:
        result["sync_result"] = "blocked"
        result["reason"] = "Pre-task git check blocked because the current branch could not be detected."
        return result
    working_tree = classify_pre_task_working_tree(repo)
    result["working_tree_detected"] = bool(working_tree.get("working_tree_detected"))
    result["working_tree_clean"] = bool(working_tree.get("working_tree_clean"))
    result["working_tree_status"] = str(working_tree.get("working_tree_status") or "")
    result["dirty_paths"] = list(working_tree.get("dirty_paths") or [])
    result["auto_stage_attempted"] = bool(working_tree.get("auto_stage_attempted"))
    result["auto_staged_paths"] = list(working_tree.get("auto_staged_paths") or [])
    result["auto_removed_paths"] = list(working_tree.get("auto_removed_paths") or [])
    result["remaining_unstaged_paths"] = list(working_tree.get("remaining_unstaged_paths") or [])
    result["working_tree_blockers"] = list(working_tree.get("working_tree_blockers") or [])
    result["working_tree_summary"] = str(working_tree.get("working_tree_summary") or "")
    if result["working_tree_blockers"]:
        result["sync_result"] = "blocked"
        result["reason"] = (
            "Pre-task git check blocked because the working tree still has ambiguous changes requiring manual review. "
            "Inspect the listed blockers before rerunning."
        )
        return result
    remotes = parse_remote_names(repo)
    current_branch = str(result.get("current_branch") or "")
    if "origin" in remotes:
        result["origin_detected"] = True
        append_git_action(result, "git fetch origin")
        fetch_code, fetch_output = run_subprocess(["git", "fetch", "origin"], repo)
        if fetch_code != 0:
            result["origin_sync_result"] = "blocked"
            result["sync_result"] = "blocked"
            result["origin_reason"] = fetch_output.strip() or "git fetch origin failed"
            result["reason"] = result["origin_reason"]
            return result
        origin_branch = f"origin/{current_branch}"
        result["origin_branch"] = origin_branch
        if git_ref_exists(repo, origin_branch):
            count_code, count_output = run_subprocess(["git", "rev-list", "--left-right", "--count", f"HEAD...{origin_branch}"], repo)
            if count_code != 0:
                result["origin_sync_result"] = "blocked"
                result["sync_result"] = "blocked"
                result["origin_reason"] = count_output.strip() or f"failed to compare HEAD with {origin_branch}"
                result["reason"] = result["origin_reason"]
                return result
            origin_ahead_count, origin_behind_count = parse_ahead_behind_counts(count_output)
            result["origin_ahead_count"] = origin_ahead_count
            result["origin_behind_count"] = origin_behind_count
            if origin_behind_count > 0:
                result["origin_sync_attempted"] = True
                result["sync_attempted"] = True
                append_git_action(result, f"git merge --no-edit {origin_branch}")
                ok, sync_reason, conflict_result = run_sync_operation_with_conflict_hook(
                    repo,
                    sync_operation="origin_sync",
                    command=["git", "merge", "--no-edit", origin_branch],
                    validation_command="",
                    no_auto_conflict_resolution_after_sync=True,
                )
                if conflict_result.get("merge_conflicts_detected"):
                    result["merge_conflict_result"] = conflict_result
                    result["origin_sync_result"] = "blocked"
                    result["sync_result"] = "blocked"
                    conflicted = ", ".join(conflict_result.get("conflicted_files") or []) or "unknown files"
                    result["origin_reason"] = sync_reason or f"origin merge produced conflicts in: {conflicted}"
                    result["reason"] = result["origin_reason"]
                    return result
                if not ok:
                    result["origin_sync_result"] = "blocked"
                    result["sync_result"] = "blocked"
                    result["origin_reason"] = sync_reason or "origin sync failed"
                    result["reason"] = result["origin_reason"]
                    return result
                result["origin_sync_result"] = "success"
            else:
                result["origin_sync_result"] = "not_needed"
        else:
            result["origin_sync_result"] = "not_needed"
            result["origin_reason"] = f"{origin_branch} does not exist"
    if "upstream" not in remotes:
        result["sync_result"] = "success" if result.get("origin_sync_result") == "success" else "not_needed"
        return result
    result["upstream_detected"] = True
    append_git_action(result, "git fetch upstream")
    fetch_code, fetch_output = run_subprocess(["git", "fetch", "upstream"], repo)
    if fetch_code != 0:
        result["sync_result"] = "blocked"
        result["reason"] = fetch_output.strip() or "git fetch upstream failed"
        return result
    upstream_branch = detect_remote_default_branch(repo, "upstream", fallback_branch="main")
    result["upstream_branch"] = upstream_branch
    count_code, count_output = run_subprocess(["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream_branch}"], repo)
    if count_code != 0:
        result["sync_result"] = "blocked"
        result["reason"] = count_output.strip() or f"failed to compare HEAD with {upstream_branch}"
        return result
    ahead_count, behind_count = parse_ahead_behind_counts(count_output)
    result["ahead_count"] = ahead_count
    result["behind_count"] = behind_count
    if behind_count <= 0:
        result["sync_result"] = "success" if result.get("origin_sync_result") == "success" else "not_needed"
        return result
    analysis = analyze_upstream_changes(repo, upstream_branch)
    result["analysis"] = analysis
    if not analysis.get("ok"):
        result["sync_result"] = "blocked"
        result["reason"] = str(analysis.get("reason") or "failed to analyze upstream changes")
        return result
    result["sync_attempted"] = True
    effective_validation_command = validation_command.strip() or latest_repo_validation_command(repo)
    append_git_action(result, f"git merge --no-edit {upstream_branch}")
    ok, sync_reason, conflict_result = run_sync_operation_with_conflict_hook(
        repo,
        sync_operation="upstream_sync",
        command=["git", "merge", "--no-edit", upstream_branch],
        validation_command=effective_validation_command,
        no_auto_conflict_resolution_after_sync=True,
    )
    if conflict_result.get("merge_conflicts_detected"):
        result["merge_conflict_result"] = conflict_result
        result["sync_result"] = "blocked"
        conflicted = ", ".join(conflict_result.get("conflicted_files") or []) or "unknown files"
        result["reason"] = sync_reason or str(conflict_result.get("blocked_reason") or f"upstream merge produced conflicts in: {conflicted}")
        return result
    if not ok:
        result["sync_result"] = "blocked"
        result["reason"] = sync_reason or "upstream sync failed"
        return result
    validation_run = run_repo_validation_command(
        repo,
        effective_validation_command,
        mode="upstream-sync",
        confidence="upstream-sync",
        target=target,
    )
    result["validation_result_after_sync"] = str(validation_run.get("validation_result") or "blocked")
    if not validation_run.get("ok"):
        result["sync_result"] = "blocked"
        result["reason"] = str(validation_run.get("reason") or "validation failed after upstream sync")
        return result
    result["sync_result"] = "success"
    return result


def current_validation_fingerprint(repo: Path) -> str:
    if not is_git_repo(repo):
        return ""
    publish_changes = classify_publishable_changes(repo)
    return compute_meaningful_content_fingerprint(repo, publish_changes)


def publish_docs_targets(repo: Path) -> list[str]:
    candidates = [
        repo / "README.md",
        repo / "docs" / "RUNBOOK.md",
        repo / "docs" / "TROUBLESHOOTING.md",
    ]
    targets: list[str] = []
    for path in candidates:
        if path.exists() and path.is_file():
            try:
                targets.append(str(path.resolve().relative_to(repo.resolve())))
            except ValueError:
                continue
    return targets


def is_docs_path(path: str) -> bool:
    rel = path.strip()
    return rel == "README.md" or rel.startswith("docs/") or rel.endswith(".md")


def load_docs_state(repo: Path) -> dict:
    return load_json_file(
        state_storage_path(repo, DOCS_STATE_FILE_NAME),
        {
            "last_refresh_mode": "",
            "last_targets": [],
            "last_reason": "",
            "ts": 0,
        },
    )


def operator_diff_text(repo: Path, changed_paths: list[str]) -> str:
    if not changed_paths:
        return ""
    code, output = run_subprocess(["git", "diff", "--", *changed_paths], repo)
    return output if code == 0 else ""


def detect_stale_doc_signals(repo: Path) -> list[str]:
    signals: list[str] = []
    for rel_path in PRIMARY_OPERATOR_DOC_FILES:
        path = repo / rel_path
        if not path.exists():
            signals.append(f"missing operator doc: {rel_path}")
    return signals


def extract_operator_doc_change_categories(diff_text: str, changed_paths: list[str]) -> list[str]:
    categories: set[str] = set()
    lowered = (diff_text or "").lower()
    changed = set(changed_paths or [])
    if any(path.startswith("scripts/") for path in changed):
        categories.add("wrappers")
    if "local_fix_agent.py" in changed:
        if re.search(r"parser\.add_argument|help=", diff_text):
            categories.add("cli")
        if re.search(r"publish|pull request|mergeable|finalizer|fixpublish", lowered):
            categories.add("publish")
        if re.search(r'control_path|blocked|manual merge required', lowered):
            categories.add("blocked_state")
    return sorted(categories)


def choose_docs_refresh_mode(repo: Path, categories: list[str], stale_signals: list[str]) -> str:
    if not categories and not stale_signals:
        return "none"
    docs_state = load_docs_state(repo)
    last_mode = str(docs_state.get("last_refresh_mode") or "").strip()
    if len(categories) >= 2 or stale_signals:
        return "rewrite"
    if last_mode == "rewrite":
        return "patch"
    return "rewrite"


def choose_docs_targets(categories: list[str], stale_signals: list[str], refresh_mode: str) -> list[str]:
    if refresh_mode == "none":
        return []
    if refresh_mode == "rewrite" or stale_signals:
        return PRIMARY_OPERATOR_DOC_FILES[:]
    targets: set[str] = {"README.md", "docs/RUNBOOK.md"}
    if any(name in categories for name in {"publish", "blocked_state"}):
        targets.add("docs/TROUBLESHOOTING.md")
    return [path for path in PRIMARY_OPERATOR_DOC_FILES if path in targets]


def assess_docs_impact(repo: Path, changed_paths: list[str]) -> dict:
    normalized = sorted(dict.fromkeys(path for path in changed_paths if path))
    diff_text = operator_diff_text(repo, normalized)
    categories = extract_operator_doc_change_categories(diff_text, normalized)
    stale_signals = detect_stale_doc_signals(repo)
    docs_required = bool(categories or stale_signals)
    refresh_mode = choose_docs_refresh_mode(repo, categories, stale_signals) if docs_required else "none"
    return {
        "docs_required": docs_required,
        "docs_targets": choose_docs_targets(categories, stale_signals, refresh_mode),
        "docs_reason": "; ".join(categories or stale_signals[:1]) if docs_required else "",
        "docs_refresh_mode": refresh_mode,
        "docs_categories": categories,
        "docs_stale_signals": stale_signals,
    }


def detect_publish_docs_impact(repo: Path, changed_paths: list[str], publish_current_mode: bool = False) -> dict:
    normalized = sorted(dict.fromkeys(path for path in changed_paths if path))
    docs_targets = publish_docs_targets(repo)
    docs_changed = [path for path in normalized if path in docs_targets or is_docs_path(path)]
    code_impacts: list[str] = []
    for path in normalized:
        if is_docs_path(path):
            continue
        suffix = Path(path).suffix.lower()
        if path == "local_fix_agent.py":
            code_impacts.append(path)
            continue
        if path.startswith("scripts/") or path.startswith("tests/"):
            code_impacts.append(path)
            continue
        if suffix in {".py", ".sh", ".toml", ".ini", ".json", ".yaml", ".yml"}:
            code_impacts.append(path)
    docs_required = bool(code_impacts and docs_targets and not docs_changed)
    refresh_mode = "none"
    if docs_required:
        refresh_mode = "rewrite" if "local_fix_agent.py" in code_impacts else "patch"
    return {
        "docs_required": docs_required,
        "docs_targets": docs_targets if docs_required else docs_changed,
        "docs_refresh_mode": refresh_mode,
        "docs_changed": docs_changed,
        "code_impacts": code_impacts,
        "publish_current_mode": publish_current_mode,
    }


def upsert_managed_markdown_block(path: Path, marker: str, content: str) -> bool:
    start = f"<!-- {marker}:start -->"
    end = f"<!-- {marker}:end -->"
    block = f"{start}\n{content.rstrip()}\n{end}\n"
    try:
        existing = path.read_text()
    except OSError:
        return False
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.S)
    if pattern.search(existing):
        updated = pattern.sub(block, existing, count=1)
    else:
        updated = existing.rstrip() + "\n\n" + block
    if updated == existing:
        return False
    try:
        path.write_text(updated)
    except OSError:
        return False
    return True


def docs_publish_block_for_target(target: str, refresh_mode: str) -> str:
    if target == "README.md":
        return "\n".join(
            [
                "## Pre-Publish Docs Gate",
                "",
                "Before a real publish, the agent now runs a documentation impact check.",
                "If code or operator-facing behavior changed and docs are stale, it updates the tracked docs before publish, reruns validation, and only then continues with push/PR work.",
                f"Current docs refresh policy: `{refresh_mode}` when docs drift is detected.",
            ]
        )
    if target == "docs/RUNBOOK.md":
        return "\n".join(
            [
                "## Pre-Publish Docs Check",
                "",
                "Real publish now includes a docs gate after validation succeeds and before branch/commit/push work starts.",
                "The agent detects documentation impact, refreshes affected docs in the same change set, reruns validation, and blocks publish if docs repair or revalidation fails.",
                f"Default docs refresh mode when triggered: `{refresh_mode}`.",
            ]
        )
    if target == "docs/TROUBLESHOOTING.md":
        return "\n".join(
            [
                "## Publish Blocked By Docs Drift",
                "",
                "If the pre-publish docs gate detects that operator docs need updates and automatic refresh or revalidation fails, publish is blocked.",
                "The publish summary reports `docs_required`, `docs_updated`, `docs_refresh_mode`, and the affected `docs_targets` so the block reason is explicit.",
            ]
        )
    return ""


def apply_publish_docs_updates(repo: Path, docs_check: dict) -> dict:
    updated_targets: list[str] = []
    for target in docs_check.get("docs_targets", []) or []:
        content = docs_publish_block_for_target(target, str(docs_check.get("docs_refresh_mode") or "patch"))
        if not content:
            continue
        marker = "fix-agent-prepublish-docs"
        if target.endswith("RUNBOOK.md"):
            marker = "fix-agent-prepublish-runbook"
        elif target.endswith("TROUBLESHOOTING.md"):
            marker = "fix-agent-prepublish-troubleshooting"
        path = repo / target
        if upsert_managed_markdown_block(path, marker, content):
            updated_targets.append(target)
    return {
        "ok": True,
        "updated": bool(updated_targets),
        "updated_targets": updated_targets,
        "reason": "" if updated_targets or docs_check.get("docs_targets") else "no docs targets available for update",
    }


def revalidate_publish_docs(repo: Path, test_cmd: str, publish_current_mode: bool = False) -> dict:
    if CURRENT_VALIDATION_PLAN.get("active"):
        validation = run_validation_stack(repo, CURRENT_VALIDATION_PLAN, include_syntax=True)
        return {
            "ran": True,
            "ok": bool(validation.get("ok")),
            "command": CURRENT_VALIDATION_PLAN.get("primary_command", ""),
            "output": validation.get("output", ""),
        }
    stripped = str(test_cmd or "").strip()
    if stripped and not stripped.startswith("n/a (publish current repo state)"):
        code, output = run_subprocess(stripped, repo, shell=True)
        return {"ran": True, "ok": code == 0, "command": stripped, "output": output.strip()}
    return {"ran": False, "ok": True, "command": "", "output": ""}


def run_prepublish_docs_stage(repo: Path, test_cmd: str, changed_paths: list[str], publish_current_mode: bool = False) -> dict:
    docs_check = detect_publish_docs_impact(repo, changed_paths, publish_current_mode=publish_current_mode)
    result = {
        "docs_checked_at_publish": True,
        "docs_required": bool(docs_check.get("docs_required")),
        "docs_updated": False,
        "docs_refresh_mode": str(docs_check.get("docs_refresh_mode") or "none"),
        "docs_targets": list(docs_check.get("docs_targets") or []),
        "blocked": False,
        "reason": "",
        "revalidated": False,
        "revalidation_command": "",
        "updated_targets": [],
    }
    if not result["docs_required"]:
        return result
    update_result = apply_publish_docs_updates(repo, docs_check)
    if not update_result.get("ok"):
        result["blocked"] = True
        result["reason"] = update_result.get("reason") or "docs update failed before publish"
        return result
    result["docs_updated"] = bool(update_result.get("updated"))
    result["updated_targets"] = list(update_result.get("updated_targets") or [])
    revalidation = revalidate_publish_docs(repo, test_cmd, publish_current_mode=publish_current_mode)
    result["revalidated"] = bool(revalidation.get("ran"))
    result["revalidation_command"] = str(revalidation.get("command") or "")
    if not revalidation.get("ok"):
        result["blocked"] = True
        result["reason"] = (
            "docs update completed, but revalidation failed: "
            + (str(revalidation.get("output") or "").strip() or "(no output)")
        )
    return result


def summarize_docs_publish_reporting(
    docs_check_performed: bool,
    docs_required: bool,
    docs_updated: bool,
    blocked: bool = False,
    reason: str = "",
) -> dict:
    normalized_reason = str(reason or "").strip()
    if not docs_check_performed:
        return {
            "docs_check_performed": False,
            "docs_status": "up_to_date",
            "docs_reason": "documentation check not performed",
        }
    if blocked:
        return {
            "docs_check_performed": True,
            "docs_status": "required_but_blocked",
            "docs_reason": normalized_reason or "docs changes required but validation/publish blocked",
        }
    if docs_updated:
        return {
            "docs_check_performed": True,
            "docs_status": "updated",
            "docs_reason": "documentation updated due to code changes",
        }
    return {
        "docs_check_performed": True,
        "docs_status": "up_to_date",
        "docs_reason": "no documentation changes detected",
    }


def repo_files(repo: Path) -> list[str]:
    if is_remote_repo(repo):
        code, output = run_subprocess(
            f"find {shlex.quote(str(repo))} -type f",
            repo,
            shell=True,
        )
        if code != 0:
            return []
        prefix = str(PurePosixPath(str(repo))) + "/"
        files = []
        for line in output.splitlines():
            candidate = line.strip()
            if not candidate.startswith(prefix):
                continue
            rel = candidate[len(prefix):]
            parts = PurePosixPath(rel).parts
            if any(part in IGNORE_DIRS for part in parts):
                continue
            files.append(rel)
        files.sort()
        return files
    files = []
    for path in repo.rglob("*"):
        rel = path.relative_to(repo)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if path.is_file():
            files.append(str(rel))
    files.sort()
    return files


def filter_unified_diff_text(output: str) -> str:
    if not output:
        return output

    kept = []
    current = []
    keep_current = True

    def flush():
        nonlocal current, keep_current
        if current and keep_current:
            kept.extend(current)
        current = []
        keep_current = True

    for line in output.splitlines():
        if line.startswith("diff --git "):
            flush()
        current.append(line)
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                a_path = parts[2][2:] if parts[2].startswith("a/") else parts[2]
                b_path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                keep_current = not (is_ignored_change_path(a_path) or is_ignored_change_path(b_path))
            else:
                keep_current = True
    flush()
    return "\n".join(kept).strip()


def filtered_git_diff_output(repo: Path, paths: list[str] | None = None) -> str:
    if paths is not None and not paths:
        return ""

    cmd = ["git", "diff"]
    if paths:
        cmd.extend(["--", *paths])

    code, output = run_subprocess(cmd, repo)
    if code != 0:
        return output
    return filter_unified_diff_text(output)


def validate_structural_safety(repo: Path, paths: list[str]) -> tuple[bool, str, str]:
    py_paths = [path for path in paths if path.endswith(".py")]
    if not py_paths:
        return True, "", FAILURE_UNKNOWN

    code, output = run_subprocess([sys.executable, "-m", "py_compile", *py_paths], repo)
    if code != 0:
        return False, output.strip(), classify_failure_type(output)

    module_paths = []
    for path in py_paths:
        rel = Path(path)
        if is_test_file_path(path) or rel.name == "__init__.py":
            continue
        module_paths.append(".".join(rel.with_suffix("").parts))

    if module_paths:
        import_code = (
            "import importlib, sys\n"
            "mods = sys.argv[1:]\n"
            "for mod in mods:\n"
            "    importlib.import_module(mod)\n"
        )
        code, output = run_subprocess([sys.executable, "-c", import_code, *module_paths], repo)
        if code != 0:
            return False, output.strip(), classify_failure_type(output)

    return True, "", FAILURE_UNKNOWN


def build_targeted_test_command(test_cmd: str, failure_context: dict) -> str:
    test_name = failure_context.get("failing_test_name", "")
    if not test_name:
        return ""
    stripped = test_cmd.strip()
    if stripped.startswith("pytest"):
        return f"{stripped} {test_name}"
    if stripped.startswith("python -m pytest"):
        return f"{stripped} {test_name}"
    return ""


def validate_patch_in_sandbox(repo: Path, test_cmd: str, failure_context: dict) -> dict:
    changed_paths = meaningful_changed_paths(repo)
    diff_text = filtered_git_diff_output(repo, changed_paths)
    if not diff_text.strip():
        return {"ok": True, "status": "neutral", "output": "", "failure_type": FAILURE_UNKNOWN}

    with tempfile.TemporaryDirectory(prefix="fix-agent-precommit-") as tmpdir:
        sandbox = Path(tmpdir) / "repo"
        shutil.copytree(
            repo,
            sandbox,
            ignore=shutil.ignore_patterns(".git", *IGNORE_DIRS, "*.bak", MEMORY_FILE_NAME),
        )

        structural_ok, structural_output, structural_failure_type = validate_structural_safety(
            sandbox,
            changed_paths,
        )
        if not structural_ok:
            return {
                "ok": False,
                "status": "rejected",
                "output": structural_output,
                "failure_type": structural_failure_type,
                "reason": "syntax/import validation failed in sandbox",
            }

        targeted_cmd = build_targeted_test_command(test_cmd, failure_context)
        validation_results = []
        if targeted_cmd:
            code, output = run_subprocess(targeted_cmd, sandbox, shell=True)
            validation_results = [{"ok": code == 0, "kind": "targeted_test", "command": targeted_cmd, "output": output.strip()}]
            if code != 0:
                sandbox_failure_context = extract_failure_context(output, sandbox)
                return {
                    "ok": False,
                    "status": evaluate_test_progress(failure_context, sandbox_failure_context, False),
                    "output": output,
                    "failure_type": classify_failure_type(output),
                    "reason": "targeted failing test did not validate in sandbox",
                }
        elif CURRENT_VALIDATION_PLAN.get("active"):
            validation_result = run_validation_stack(sandbox, CURRENT_VALIDATION_PLAN, include_syntax=True)
            validation_results = validation_result.get("results", [])
            if not validation_result.get("ok"):
                failed_step = validation_result.get("failed_step", {})
                return {
                    "ok": False,
                    "status": "rejected",
                    "output": validation_result.get("output", ""),
                    "failure_type": validation_result.get("failure_type", FAILURE_UNKNOWN),
                    "reason": f"discovered validation failed at {failed_step.get('kind', 'unknown')} step",
                    "validation_results": validation_result.get("results", []),
                }

        return {
            "ok": True,
            "status": "passed",
            "output": "",
            "failure_type": FAILURE_UNKNOWN,
            "reason": "pre-commit validation passed",
            "validation_results": validation_results,
        }


def score_candidate_validation(result: dict) -> int:
    if result.get("ok"):
        return 3
    status = result.get("status", "")
    failure_type = result.get("failure_type", FAILURE_UNKNOWN)
    if failure_type in {FAILURE_SYNTAX_ERROR, FAILURE_IMPORT_ERROR}:
        return -3
    if status == "different failure appears":
        return -2
    if status == "same failure persists":
        return -1
    return 0


def build_candidate_patches(
    repo: Path,
    primary_file: str,
    best_attempt: dict | None,
    current_strategy_type: str,
) -> list[dict]:
    candidates = []
    current_paths = meaningful_changed_paths(repo)
    current_diff = filtered_git_diff_output(repo, current_paths)
    if current_diff.strip():
        candidates.append(
            {
                "name": "current_patch",
                "strategy_type": current_strategy_type or "fallback/default",
                "diff_text": current_diff,
            }
        )

    if primary_file:
        focused_diff = filtered_git_diff_output(repo, [primary_file])
        if focused_diff.strip() and focused_diff != current_diff:
            candidates.append(
                {
                    "name": "precision_focus",
                    "strategy_type": "minimal_patch",
                    "diff_text": focused_diff,
                }
            )

    if best_attempt and best_attempt.get("diff_text") and best_attempt.get("diff_text") != current_diff:
        candidates.append(
            {
                "name": "best_attempt_reuse",
                "strategy_type": best_attempt.get("strategy_type", "fallback/default"),
                "diff_text": best_attempt["diff_text"],
            }
        )

    return candidates[:3]


def validate_candidate_patch(
    repo: Path,
    test_cmd: str,
    failure_context: dict,
    candidate: dict,
) -> dict:
    diff_text = candidate.get("diff_text", "")
    if not diff_text.strip():
        return {
            "ok": False,
            "status": "rejected",
            "output": "Empty candidate diff.",
            "failure_type": FAILURE_UNKNOWN,
            "reason": "candidate diff was empty",
        }

    with tempfile.TemporaryDirectory(prefix="fix-agent-candidate-") as tmpdir:
        sandbox = Path(tmpdir) / "repo"
        shutil.copytree(
            repo,
            sandbox,
            ignore=shutil.ignore_patterns(*IGNORE_DIRS, "*.bak", MEMORY_FILE_NAME),
        )
        run_subprocess(["git", "restore", "."], sandbox)
        applied, apply_output = apply_diff_snapshot(sandbox, diff_text)
        if not applied:
            return {
                "ok": False,
                "status": "rejected",
                "output": apply_output,
                "failure_type": FAILURE_UNKNOWN,
                "reason": "candidate patch could not be applied in sandbox",
            }

        result = validate_patch_in_sandbox(sandbox, test_cmd, failure_context)
        result["candidate_name"] = candidate.get("name", "")
        result["strategy_type"] = candidate.get("strategy_type", "")
        result["candidate_score"] = score_candidate_validation(result)
        return result


def select_best_candidate(repo: Path, test_cmd: str, failure_context: dict, candidates: list[dict]) -> dict:
    results = []
    best_index = -1
    for index, candidate in enumerate(candidates, start=1):
        progress(f"generating candidates..." if index == 1 else f"validating candidate {index}/{len(candidates)}...")
        if index == 1:
            progress(f"validating candidate {index}/{len(candidates)}...")
        result = validate_candidate_patch(repo, test_cmd, failure_context, candidate)
        if result.get("ok"):
            progress("accepted")
        else:
            reason = result.get("reason") or result.get("status") or "validation failed"
            progress(f"rejected ({reason})")
        result["index"] = index
        results.append(result)
        if best_index == -1:
            best_index = 0
            continue
        current_best = results[best_index]
        if result.get("ok") and not current_best.get("ok"):
            best_index = len(results) - 1
        elif result.get("ok") == current_best.get("ok") and result.get("candidate_score", -99) > current_best.get("candidate_score", -99):
            best_index = len(results) - 1

    chosen = results[best_index] if best_index >= 0 else {}
    if chosen:
        progress(
            "chosen candidate "
            f"{chosen.get('candidate_name', '')} "
            f"(strategy={chosen.get('strategy_type', '')}, score={chosen.get('candidate_score', '')})"
        )
    return {"results": results, "chosen": chosen}


def apply_candidate_to_repo(repo: Path, candidate_diff_text: str) -> tuple[bool, str]:
    changed_paths = meaningful_changed_paths(repo)
    if changed_paths:
        code, output = run_subprocess(["git", "restore", "--", *changed_paths], repo)
        if code != 0:
            return False, output
    return apply_diff_snapshot(repo, candidate_diff_text)


def apply_diff_snapshot(repo: Path, diff_text: str) -> tuple[bool, str]:
    if not diff_text.strip():
        return False, "No diff snapshot available."

    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as tmp:
        tmp.write(diff_text)
        tmp_path = Path(tmp.name)

    try:
        code, output = run_subprocess(["git", "apply", str(tmp_path)], repo)
        return code == 0, output
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def make_commit_message(summary: str) -> str:
    first_line = summary.strip().splitlines()[0] if summary.strip() else ""
    normalized = re.sub(r"\s+", " ", first_line).strip(" .")
    if not normalized:
        normalized = "fix failing tests"
    if len(normalized) > 60:
        normalized = normalized[:57].rstrip() + "..."
    return f"agent: {normalized}"


def normalize_failure_output(output: str) -> str:
    text = output.lower()
    text = re.sub(r"\b\d+\.\d+s\b", "<time>", text)
    text = re.sub(r"\b\d+\b", "<num>", text)
    text = re.sub(r"0x[0-9a-f]+", "<hex>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def should_track_modified_file(tool_name: str, result: str) -> str | None:
    if tool_name not in {"write_file", "replace_in_file", "append_to_file"}:
        return None
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return None
    if data.get("ok") is not True:
        return None
    path = data.get("path")
    return path if isinstance(path, str) and path else None


def classify_failure_type(output: str) -> str:
    normalized = normalize_failure_output(output)

    if any(token in normalized for token in ["syntaxerror", "indentationerror", "taberror"]):
        return FAILURE_SYNTAX_ERROR
    if any(token in normalized for token in ["modulenotfounderror", "importerror"]):
        return FAILURE_IMPORT_ERROR
    if "assertionerror" in normalized or " failed" in normalized:
        return FAILURE_ASSERTION_FAILURE
    if "error" in normalized or "exception" in normalized or "traceback" in normalized:
        return FAILURE_RUNTIME_ERROR
    return FAILURE_UNKNOWN


def extract_failure_count(output: str) -> int | None:
    match = re.search(r"=+\s+(.+?)\s+in\s+\d", output, re.I | re.S)
    summary = match.group(1) if match else output
    counts = re.findall(r"(\d+)\s+failed", summary, re.I)
    if counts:
        return sum(int(value) for value in counts)
    return None


def extract_test_expectation(output: str) -> str:
    test_name = ""
    test_match = re.search(r"FAILED\s+([\w/.-]+::[\w\[\]-]+)", output)
    if test_match:
        test_name = test_match.group(1)

    assertion_line = ""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("E       "):
            assertion_line = stripped[8:].strip()
            break

    expected = ""
    actual = ""
    compare_match = re.search(r"assert\s+(.+?)\s*==\s*(.+)", assertion_line)
    if compare_match:
        actual = compare_match.group(1).strip()
        expected = compare_match.group(2).strip()
    elif assertion_line:
        expected = assertion_line

    parts = []
    if test_name:
        parts.append(f"Test: {test_name}")
    if expected:
        parts.append(f"What the test expects: {expected}")
    else:
        parts.append("What the test expects: infer from the failing assertion and test body.")
    if actual:
        parts.append(f"What the current behavior is: {actual}")
    else:
        parts.append("What the current behavior is: infer from the failure output and current implementation.")
    parts.append("What needs to change: make the implementation satisfy the failing test expectation.")
    return "\n".join(parts)


def extract_failure_context(output: str, repo: Path | None = None) -> dict:
    test_name = ""
    test_match = re.search(r"FAILED\s+([\w/.-]+::[\w\[\]-]+)", output)
    if test_match:
        test_name = test_match.group(1)

    assertion_line = ""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("E       "):
            assertion_line = stripped[8:].strip()
            break

    expected = ""
    actual = ""
    compare_match = re.search(r"assert\s+(.+?)\s*==\s*(.+)", assertion_line)
    if compare_match:
        actual = compare_match.group(1).strip()
        expected = compare_match.group(2).strip()

    stack_frames = []
    for match in re.findall(r'File "([^"]+\.py)", line (\d+), in ([^\n]+)', output):
        path, lineno, func = match
        candidate = path.strip()
        if repo and candidate.startswith(str(repo)):
            try:
                candidate = str(Path(candidate).resolve().relative_to(repo))
            except ValueError:
                pass
        stack_frames.append(
            {
                "path": candidate,
                "line": int(lineno),
                "function": func.strip(),
            }
        )

    return {
        "failing_test_name": test_name,
        "failing_assertion": assertion_line,
        "expected_value": expected,
        "actual_value": actual,
        "stack_frames": stack_frames[:5],
    }


def normalize_repo_relative_python_path(path: str, repo: Path) -> str:
    candidate = path.strip()
    if not candidate:
        return ""
    try:
        candidate_path = Path(candidate)
        if candidate_path.is_absolute():
            return str(candidate_path.resolve().relative_to(repo))
    except (OSError, ValueError):
        return ""
    normalized = candidate.replace("\\", "/").lstrip("./")
    if normalized in set(repo_files(repo)):
        return normalized
    return ""


def extract_validation_path_references(output: str, repo: Path) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []

    def add(candidate: str) -> None:
        normalized = normalize_repo_relative_python_path(candidate, repo)
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)

    for match in re.findall(r'File "([^"]+\.py)"', output):
        add(match)
    for match in re.findall(r"((?:\./)?(?:tests?/)?[\w./-]+\.py):\d+", output):
        add(match)
    for match in re.findall(r"(ERROR collecting\s+((?:\./)?(?:tests?/)?[\w./-]+\.py))", output):
        add(match[1])
    for match in re.findall(r"FAILED\s+((?:\./)?(?:tests?/)?[\w./-]+\.py)(?:::.*)?", output):
        add(match)

    return paths


def extract_validation_test_files(output: str, repo: Path, failure_context: dict) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []

    def add(candidate: str) -> None:
        normalized = normalize_repo_relative_python_path(candidate, repo)
        if normalized and normalized not in seen and is_test_file_path(normalized):
            seen.add(normalized)
            files.append(normalized)

    test_name = str(failure_context.get("failing_test_name") or "")
    if "::" in test_name:
        add(test_name.split("::", 1)[0])

    for match in re.findall(r"FAILED\s+((?:\./)?(?:tests?/)?[\w./-]+\.py)(?:::.*)?", output):
        add(match)
    for match in re.findall(r"ERROR collecting\s+((?:\./)?(?:tests?/)?[\w./-]+\.py)", output):
        add(match)
    for path in extract_validation_path_references(output, repo):
        if is_test_file_path(path):
            add(path)

    return files[:5]


def extract_module_import_candidates(output: str, repo: Path) -> list[str]:
    all_repo_files = set(repo_files(repo))
    seen: set[str] = set()
    candidates: list[str] = []

    def add(path: str) -> None:
        if path in all_repo_files and path not in seen:
            seen.add(path)
            candidates.append(path)

    module_names: set[str] = set()
    for match in re.findall(r"(?:ModuleNotFoundError|ImportError):.*?'([^']+)'", output):
        module_names.add(match.strip())
    for match in re.findall(r"(?:from|import)\s+([a-zA-Z_][\w.]*)", output):
        module_names.add(match.strip())

    for module in module_names:
        module_path = module.replace(".", "/")
        add(f"{module_path}.py")
        add(f"{module.split('.')[-1]}.py")
        add(f"tests/{module_path}.py")

    return candidates[:5]


def map_validation_error_type(failure_type: str, failure_context: dict, output: str) -> str:
    normalized = normalize_failure_output(output)
    if is_invalid_validation_command_output(output):
        return "invalid_validation_command"
    if failure_type == FAILURE_SYNTAX_ERROR:
        return "syntax"
    if failure_type == FAILURE_IMPORT_ERROR:
        return "import"
    if failure_context.get("failing_test_name"):
        if failure_context.get("failing_assertion") or "assertionerror" in normalized:
            return "assertion_mismatch"
        return "failing_test"
    if any(token in normalized for token in ["traceback", "error collecting", "command not found", "failed", "exception"]):
        return "command_failure"
    return "unknown"


def summarize_failure_context_snippet(output: str, limit: int = 800) -> str:
    snippet = (output or "").strip()
    snippet = re.sub(r"\n{3,}", "\n\n", snippet)
    return snippet[:limit]


def extract_failure_line_numbers(failure_context: dict, output: str = "") -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for frame in failure_context.get("stack_frames", []):
        line = frame.get("line")
        if isinstance(line, int) and line > 0 and line not in seen:
            seen.add(line)
            numbers.append(line)
    for match in re.findall(r'File "[^"]+\.py", line (\d+)', output):
        line = int(match)
        if line > 0 and line not in seen:
            seen.add(line)
            numbers.append(line)
    return numbers[:10]


def build_validation_repair_target_details(
    validation_error_type: str,
    failing_test_files: list[str],
    failing_source_files: list[str],
    traceback_files: list[str],
    precision_patch: dict,
) -> list[dict]:
    details: list[dict] = []
    seen: set[str] = set()

    def add(path: str, target_type: str, confidence: str, reason: str) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        details.append(
            {
                "target_path": path,
                "target_type": target_type,
                "target_confidence": confidence,
                "target_reason": reason,
            }
        )

    if precision_patch.get("file"):
        precision_reason = str(precision_patch.get("reason") or "high-confidence target from traceback or validation output")
        if validation_error_type == "import":
            precision_reason = "explicit source file from import failure"
        elif validation_error_type == "syntax":
            precision_reason = "explicit source file from py_compile or syntax traceback"
        add(
            str(precision_patch.get("file")),
            "source" if not is_test_file_path(str(precision_patch.get("file"))) else "test",
            "high",
            precision_reason,
        )

    for path in failing_source_files:
        if path in traceback_files or validation_error_type in {"syntax", "import"}:
            confidence = "high"
            reason = "explicit source file from traceback, py_compile, or import failure"
        else:
            confidence = "medium"
            reason = "pytest failure referenced this source file"
        add(path, "source", confidence, reason)

    for path in failing_test_files:
        confidence = "low"
        reason = "only failing test ownership is clear"
        if not failing_source_files:
            confidence = "medium"
            reason = "failing test file identified from pytest output"
        add(path, "test", confidence, reason)

    return details[:5]


def format_validation_failure_analysis(analysis: dict) -> str:
    return "\n".join(
        [
            "=== VALIDATION FAILURE ANALYSIS ===",
            f"validation_error_type: {analysis.get('validation_error_type') or 'unknown'}",
            f"failing_command: {analysis.get('failing_command') or '(none)'}",
            f"failing_test_files: {analysis.get('failing_test_files') or []}",
            f"failing_source_files: {analysis.get('failing_source_files') or []}",
            f"traceback_files: {analysis.get('traceback_files') or []}",
            f"failure_line_numbers: {analysis.get('failure_line_numbers') or []}",
            f"repair_targets: {analysis.get('repair_targets') or []}",
            f"repair_target_details: {analysis.get('repair_target_details') or []}",
            f"target_confidence: {analysis.get('target_confidence') or 'low'}",
            f"target_reason: {analysis.get('target_reason') or '(none)'}",
            f"repair_context_used: {format_bool(analysis.get('repair_context_used'))}",
            f"repair_goal: {analysis.get('repair_goal') or '(none)'}",
            f"failure_context_snippet: {analysis.get('failure_context_snippet') or '(none)'}",
        ]
    )


def latest_failed_validation_run(repo: Path) -> dict:
    repo_str = str(repo.resolve())
    state = load_recent_state()
    recent_runs = [item for item in state.get("recent_runs", []) if isinstance(item, dict)]
    for item in reversed(recent_runs):
        if str(item.get("repo") or "") != repo_str:
            continue
        result = str(item.get("validation_result") or "").strip()
        if result in {"failed", "blocked"}:
            return item
    return {}


def analyze_validation_failure(
    repo: Path,
    *,
    publish_output: str = "",
    validation_command: str = "",
    validation_output: str = "",
) -> dict:
    recent_failure = latest_failed_validation_run(repo)
    command = str(validation_command or recent_failure.get("validation_command") or "").strip()
    if not command:
        match = re.search(r"validation_command:\s*(.+)", publish_output)
        if match:
            command = match.group(1).strip()

    output = str(validation_output or recent_failure.get("blocked_reason") or "").strip()
    if not output:
        reason_match = re.search(r"validation_record_reason:\s*(.+)", publish_output)
        if reason_match:
            output = reason_match.group(1).strip()
    combined_output = "\n".join(part for part in [output, publish_output] if part).strip()
    failure_context = extract_failure_context(combined_output, repo)
    failure_type = classify_failure_type(combined_output)
    validation_error_type = map_validation_error_type(failure_type, failure_context, combined_output)
    failing_test_files = extract_validation_test_files(combined_output, repo, failure_context)
    traceback_files = [
        str(frame.get("path") or "")
        for frame in failure_context.get("stack_frames", [])
        if str(frame.get("path") or "")
    ]
    for path in extract_validation_path_references(combined_output, repo):
        if path not in traceback_files:
            traceback_files.append(path)
    traceback_files = traceback_files[:5]
    failure_line_numbers = extract_failure_line_numbers(failure_context, combined_output)
    relevant_context = extract_relevant_file_context(
        repo,
        combined_output,
        list(recent_failure.get("files_changed") or []),
    )
    precision_patch = extract_precision_patch_context(combined_output, relevant_context, failure_context)

    explicit_source_candidates: list[str] = []
    inferred_source_candidates: list[str] = []
    seen_sources: set[str] = set()

    def add_source(candidate: str, *, explicit: bool) -> None:
        normalized = normalize_repo_relative_python_path(candidate, repo) if candidate else ""
        if normalized and normalized not in seen_sources and not is_test_file_path(normalized):
            seen_sources.add(normalized)
            if explicit:
                explicit_source_candidates.append(normalized)
            else:
                inferred_source_candidates.append(normalized)

    for frame in failure_context.get("stack_frames", []):
        add_source(str(frame.get("path") or ""), explicit=True)
    for path in extract_validation_path_references(combined_output, repo):
        if not is_test_file_path(path):
            add_source(path, explicit=True)
    for path in extract_module_import_candidates(combined_output, repo):
        add_source(path, explicit=True)
    if explicit_source_candidates or failure_context.get("stack_frames"):
        for path in relevant_context.get("required_impl_files", []):
            add_source(path, explicit=False)

    if not explicit_source_candidates and validation_error_type not in {"syntax", "import"}:
        precision_patch = {"active": False, "file": "", "symbol": "", "reason": ""}

    source_candidates = explicit_source_candidates + [path for path in inferred_source_candidates if path not in explicit_source_candidates]

    repair_targets: list[str] = []
    if precision_patch.get("file"):
        repair_targets.append(str(precision_patch.get("file")))
    repair_targets.extend(path for path in source_candidates if path not in repair_targets)
    if not repair_targets:
        repair_targets.extend(path for path in failing_test_files if path not in repair_targets)

    repair_targets = repair_targets[:3]
    repair_target_details = build_validation_repair_target_details(
        validation_error_type,
        failing_test_files[:5],
        source_candidates[:5],
        traceback_files,
        precision_patch,
    )
    repair_context_used = bool(combined_output and (repair_target_details or failure_context.get("failing_test_name")))
    top_target = repair_target_details[0] if repair_target_details else {}
    if validation_error_type == "syntax" and repair_targets:
        repair_goal = f"Fix the syntax error blocking validation in {repair_targets[0]}."
    elif validation_error_type == "import" and repair_targets:
        repair_goal = f"Fix the import failure blocking validation, starting with {repair_targets[0]}."
    elif validation_error_type in {"failing_test", "assertion_mismatch"} and failing_test_files:
        target_label = repair_targets[0] if repair_targets else failing_test_files[0]
        repair_goal = f"Fix the validation failure reported by {failing_test_files[0]}, starting with {target_label}."
    elif command:
        repair_goal = f"Fix the validation failure from `{command}`."
    else:
        repair_goal = "Fix the validation failure blocking publish."

    return {
        "failing_command": command,
        "validation_error_type": validation_error_type,
        "failing_test_files": failing_test_files[:5],
        "failing_source_files": source_candidates[:5],
        "traceback_files": traceback_files,
        "failure_line_numbers": failure_line_numbers,
        "failure_context_snippet": summarize_failure_context_snippet(combined_output),
        "repair_targets": repair_targets,
        "repair_target_details": repair_target_details,
        "target_confidence": str(top_target.get("target_confidence") or ("low" if not repair_context_used else "medium")),
        "target_reason": str(top_target.get("target_reason") or ("no high-confidence target inferred" if not repair_context_used else "validation analysis inferred a likely target")),
        "repair_goal": repair_goal,
        "repair_context_used": repair_context_used,
        "failing_test_name": str(failure_context.get("failing_test_name") or ""),
        "failing_assertion": str(failure_context.get("failing_assertion") or ""),
        "expected_value": str(failure_context.get("expected_value") or ""),
        "actual_value": str(failure_context.get("actual_value") or ""),
        "stack_frames": list(failure_context.get("stack_frames") or []),
        "relevant_files": list(relevant_context.get("selected") or []),
        "primary_file": str(relevant_context.get("primary_file") or ""),
        "precision_patch": dict(precision_patch),
        "analysis_source": "recent_state" if recent_failure else "publish_output",
    }


def validation_error_type_to_failure_type(error_type: str) -> str:
    mapping = {
        "syntax": FAILURE_SYNTAX_ERROR,
        "import": FAILURE_IMPORT_ERROR,
        "failing_test": FAILURE_ASSERTION_FAILURE,
        "assertion_mismatch": FAILURE_ASSERTION_FAILURE,
        "command_failure": FAILURE_RUNTIME_ERROR,
    }
    return mapping.get(str(error_type or "").strip(), FAILURE_UNKNOWN)


def repair_context_as_failure_context(repair_context: dict) -> dict:
    return {
        "failing_test_name": str(repair_context.get("failing_test_name") or ""),
        "failing_assertion": str(repair_context.get("failing_assertion") or ""),
        "expected_value": str(repair_context.get("expected_value") or ""),
        "actual_value": str(repair_context.get("actual_value") or ""),
        "stack_frames": list(repair_context.get("stack_frames") or []),
    }


def format_repair_context_note(repair_context: dict) -> str:
    if not repair_context or not repair_context.get("repair_context_used"):
        return ""
    return (
        "Publish validation failure analysis:\n"
        f"- validation_error_type: {repair_context.get('validation_error_type') or 'unknown'}\n"
        f"- failing_command: {repair_context.get('failing_command') or '(none)'}\n"
        f"- failing_test_files: {repair_context.get('failing_test_files') or []}\n"
        f"- failing_source_files: {repair_context.get('failing_source_files') or []}\n"
        f"- traceback_files: {repair_context.get('traceback_files') or []}\n"
        f"- failure_line_numbers: {repair_context.get('failure_line_numbers') or []}\n"
        f"- repair_targets: {repair_context.get('repair_targets') or []}\n"
        f"- repair_target_details: {repair_context.get('repair_target_details') or []}\n"
        f"- target_confidence: {repair_context.get('target_confidence') or 'low'}\n"
        f"- target_reason: {repair_context.get('target_reason') or '(none)'}\n"
        f"- repair_goal: {repair_context.get('repair_goal') or '(none)'}\n"
        f"- failure_context_snippet: {repair_context.get('failure_context_snippet') or '(none)'}\n"
    )


def load_pattern_memory(repo: Path) -> dict:
    path = state_storage_path(repo, MEMORY_FILE_NAME)
    if not path.exists():
        return {"patterns": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"patterns": []}
    if not isinstance(data, dict) or not isinstance(data.get("patterns"), list):
        return {"patterns": []}
    return data


def save_pattern_memory(repo: Path, memory: dict) -> None:
    path = state_storage_path(repo, MEMORY_FILE_NAME)
    try:
        path.write_text(json.dumps(memory, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def default_pattern_repo_path() -> Path:
    return Path.home() / ".codex" / "memories" / DEFAULT_PATTERN_REPO_DIR_NAME


def normalize_pattern_repo_path(path_value: str | Path | None) -> Path:
    if isinstance(path_value, Path):
        path = path_value
    elif path_value:
        path = Path(path_value)
    else:
        path = default_pattern_repo_path()
    return path.expanduser().resolve()


def pattern_repo_storage_path(pattern_repo: Path, name: str) -> Path:
    return normalize_pattern_repo_path(pattern_repo) / name


def pattern_effectiveness_path(pattern_repo: Path) -> Path:
    return pattern_repo_storage_path(pattern_repo, SCRIPT_PATTERN_EFFECTIVENESS_FILE_NAME)


def load_pattern_effectiveness(pattern_repo: Path) -> dict:
    path = pattern_effectiveness_path(pattern_repo)
    if not path.exists():
        return {"families": {}, "repos": {}, "task_types": {}, "updated_at": 0}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"families": {}, "repos": {}, "task_types": {}, "updated_at": 0}
    if not isinstance(data, dict):
        return {"families": {}, "repos": {}, "task_types": {}, "updated_at": 0}
    return {
        "families": data.get("families", {}) if isinstance(data.get("families"), dict) else {},
        "repos": data.get("repos", {}) if isinstance(data.get("repos"), dict) else {},
        "task_types": data.get("task_types", {}) if isinstance(data.get("task_types"), dict) else {},
        "updated_at": int(data.get("updated_at", 0) or 0),
    }


def save_pattern_effectiveness(pattern_repo: Path, effectiveness: dict) -> None:
    path = pattern_effectiveness_path(pattern_repo)
    payload = {
        "families": effectiveness.get("families", {}) if isinstance(effectiveness.get("families"), dict) else {},
        "repos": effectiveness.get("repos", {}) if isinstance(effectiveness.get("repos"), dict) else {},
        "task_types": effectiveness.get("task_types", {}) if isinstance(effectiveness.get("task_types"), dict) else {},
        "updated_at": int(effectiveness.get("updated_at", int(time.time())) or int(time.time())),
    }
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def effectiveness_stats_view(record: dict | None, task_type: str) -> tuple[float, float, float]:
    if not isinstance(record, dict):
        return 0.0, 0.0, 0.0
    task_stats = record.get("task_types", {}).get(task_type, {}) if isinstance(record.get("task_types"), dict) else {}
    times_applied = float(task_stats.get("times_applied", record.get("times_applied", 0)) or 0)
    times_successful = float(task_stats.get("times_successful", record.get("times_successful", 0)) or 0)
    times_overapplied = float(task_stats.get("times_overapplied", record.get("times_overapplied", 0)) or 0)
    success_rate = (times_successful / times_applied) if times_applied else 0.0
    overapply_rate = (times_overapplied / times_applied) if times_applied else 0.0
    avg_delta = float(task_stats.get("score_delta_total", record.get("score_delta_total", 0.0)) or 0.0) / times_applied if times_applied else 0.0
    return success_rate, overapply_rate, avg_delta


def update_effectiveness_stats(record: dict, task_type: str, *, considered: bool = False, applied: bool = False, successful: bool = False, overapplied: bool = False, score_delta: float = 0.0) -> None:
    if considered:
        record["times_considered"] = int(record.get("times_considered", 0) or 0) + 1
    if applied:
        record["times_applied"] = int(record.get("times_applied", 0) or 0) + 1
        record["score_delta_total"] = float(record.get("score_delta_total", 0.0) or 0.0) + float(score_delta)
    if successful:
        record["times_successful"] = int(record.get("times_successful", 0) or 0) + 1
    if overapplied:
        record["times_overapplied"] = int(record.get("times_overapplied", 0) or 0) + 1
    task_types = record.setdefault("task_types", {})
    task_record = task_types.setdefault(task_type, {})
    if considered:
        task_record["times_considered"] = int(task_record.get("times_considered", 0) or 0) + 1
    if applied:
        task_record["times_applied"] = int(task_record.get("times_applied", 0) or 0) + 1
        task_record["score_delta_total"] = float(task_record.get("score_delta_total", 0.0) or 0.0) + float(score_delta)
    if successful:
        task_record["times_successful"] = int(task_record.get("times_successful", 0) or 0) + 1
    if overapplied:
        task_record["times_overapplied"] = int(task_record.get("times_overapplied", 0) or 0) + 1


def ensure_pattern_repo(pattern_repo: Path) -> Path:
    resolved = normalize_pattern_repo_path(pattern_repo)
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / "candidates").mkdir(parents=True, exist_ok=True)
    (resolved / "curated" / "trusted").mkdir(parents=True, exist_ok=True)
    (resolved / "curated" / "experimental").mkdir(parents=True, exist_ok=True)
    return resolved


def ensure_pattern_repo_status(pattern_repo: Path) -> tuple[Path, bool]:
    resolved = normalize_pattern_repo_path(pattern_repo)
    created = not resolved.exists()
    ensured = ensure_pattern_repo(resolved)
    return ensured, created


def reset_pattern_repo(pattern_repo: Path) -> tuple[Path, bool]:
    resolved = normalize_pattern_repo_path(pattern_repo)
    existed = resolved.exists()
    if existed:
        shutil.rmtree(resolved)
    ensured = ensure_pattern_repo(resolved)
    return ensured, existed


def pattern_repo_source_catalog_path(pattern_repo: Path) -> Path:
    return pattern_repo_storage_path(pattern_repo, PATTERN_REPO_SOURCE_CATALOG_FILE_NAME)


def load_pattern_source_catalog(pattern_repo: Path) -> dict:
    path = pattern_repo_source_catalog_path(pattern_repo)
    if not path.exists():
        return {"sources": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"sources": []}
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        return {"sources": []}
    return {"sources": [item for item in data.get("sources", []) if isinstance(item, dict)]}


def save_pattern_source_catalog(pattern_repo: Path, catalog: dict) -> None:
    path = pattern_repo_source_catalog_path(pattern_repo)
    payload = {"sources": catalog.get("sources", []) if isinstance(catalog.get("sources"), list) else []}
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def load_raw_script_pattern_memory(pattern_repo: Path) -> dict:
    path = pattern_repo_storage_path(pattern_repo, SCRIPT_PATTERN_MEMORY_FILE_NAME)
    if not path.exists():
        return {"patterns": [], "sources": [], "updated_at": 0}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"patterns": [], "sources": [], "updated_at": 0}
    if not isinstance(data, dict):
        return {"patterns": [], "sources": [], "updated_at": 0}
    patterns = data.get("patterns", [])
    sources = data.get("sources", [])
    return {
        "patterns": patterns if isinstance(patterns, list) else [],
        "sources": sources if isinstance(sources, list) else [],
        "updated_at": int(data.get("updated_at", 0) or 0),
    }


def save_script_pattern_memory(pattern_repo: Path, memory: dict) -> None:
    path = pattern_repo_storage_path(pattern_repo, SCRIPT_PATTERN_MEMORY_FILE_NAME)
    payload = {
        "patterns": memory.get("patterns", []),
        "sources": memory.get("sources", []),
        "updated_at": int(memory.get("updated_at", int(time.time())) or int(time.time())),
    }
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def pattern_control_path(pattern_repo: Path) -> Path:
    return pattern_repo_storage_path(pattern_repo, SCRIPT_PATTERN_CONTROL_FILE_NAME)


def load_pattern_controls(pattern_repo: Path) -> dict:
    path = pattern_control_path(pattern_repo)
    if not path.exists():
        return {"patterns": {}, "sources": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"patterns": {}, "sources": {}}
    patterns = data.get("patterns", {}) if isinstance(data, dict) else {}
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    return {
        "patterns": patterns if isinstance(patterns, dict) else {},
        "sources": sources if isinstance(sources, dict) else {},
    }


def save_pattern_controls(pattern_repo: Path, controls: dict) -> None:
    path = pattern_control_path(pattern_repo)
    payload = {
        "patterns": controls.get("patterns", {}) if isinstance(controls.get("patterns"), dict) else {},
        "sources": controls.get("sources", {}) if isinstance(controls.get("sources"), dict) else {},
    }
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def effective_source_state(source_record: dict, override: dict | None = None) -> tuple[str, str]:
    base_state = normalize_pattern_promotion_state(source_record)
    base_trust = str(source_record.get("trust_level") or "trusted").strip().lower() or "trusted"
    if not isinstance(override, dict):
        return base_state, base_trust
    state = str(override.get("promotion_state") or base_state).strip().lower() or base_state
    trust = str(override.get("trust_level") or base_trust).strip().lower() or base_trust
    if state not in {"candidate", "curated_experimental", "curated_trusted"}:
        state = base_state
    if trust not in PATTERN_TRUST_LEVELS:
        trust = base_trust
    if state == "curated_trusted":
        trust = "trusted"
    elif state == "curated_experimental":
        trust = "experimental"
    return state, trust


def source_control_keys(source_record: dict) -> set[str]:
    keys = set()
    for field in ("id", "repo_rel_path", "candidate_path"):
        candidate = str(source_record.get(field) or "").strip()
        if candidate:
            keys.add(candidate)
    return keys


def pattern_control_keys(pattern: dict) -> set[str]:
    keys = set()
    for field in ("id", "source_repo_path", "source_file"):
        candidate = str(pattern.get(field) or "").strip()
        if candidate:
            keys.add(candidate)
    return keys


def resolve_source_override(controls: dict, source_record: dict) -> dict | None:
    source_controls = controls.get("sources", {}) if isinstance(controls, dict) else {}
    for key in source_control_keys(source_record):
        override = source_controls.get(key)
        if isinstance(override, dict):
            return override
    return None


def resolve_pattern_override(controls: dict, pattern: dict) -> dict | None:
    pattern_controls = controls.get("patterns", {}) if isinstance(controls, dict) else {}
    for key in pattern_control_keys(pattern):
        override = pattern_controls.get(key)
        if isinstance(override, dict):
            return override
    return None


def apply_manual_metadata(pattern: dict, override: dict | None = None) -> dict:
    updated = dict(pattern)
    if not isinstance(override, dict):
        return updated
    if override.get("trust_level"):
        updated["trust_level"] = str(override.get("trust_level"))
    if override.get("promotion_state"):
        updated["promotion_state"] = str(override.get("promotion_state"))
    if override.get("promotion_method"):
        updated["promotion_method"] = str(override.get("promotion_method"))
    if override.get("promotion_reason"):
        updated["promotion_reason"] = str(override.get("promotion_reason"))
    if override.get("timestamp") is not None:
        updated["timestamp"] = int(override.get("timestamp") or 0)
    return updated


def load_effective_pattern_sources(pattern_repo: Path) -> list[dict]:
    repo_root = ensure_pattern_repo(pattern_repo)
    catalog = load_pattern_source_catalog(repo_root)
    controls = load_pattern_controls(repo_root)
    effective: list[dict] = []
    for source in [item for item in catalog.get("sources", []) if isinstance(item, dict)]:
        override = resolve_source_override(controls, source)
        if isinstance(override, dict) and override.get("action") == "forget":
            continue
        state, trust = effective_source_state(source, override)
        updated = dict(source)
        updated["effective_promotion_state"] = state
        updated["effective_trust_level"] = trust
        updated["promotion_method"] = str((override or {}).get("promotion_method") or source.get("promotion_method") or ("manual" if override else "automatic"))
        updated["promotion_reason"] = str((override or {}).get("promotion_reason") or source.get("promotion_reason") or "")
        updated["promotion_timestamp"] = int((override or {}).get("timestamp") or source.get("imported_at") or 0)
        effective.append(updated)
    return effective


def build_effective_pattern_memory(pattern_repo: Path) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    raw_memory = load_raw_script_pattern_memory(repo_root)
    controls = load_pattern_controls(repo_root)
    effective_sources = load_effective_pattern_sources(repo_root)
    source_by_path = {
        str(source.get("repo_rel_path") or source.get("candidate_path") or ""): source
        for source in effective_sources
        if str(source.get("repo_rel_path") or source.get("candidate_path") or "")
    }
    retained: list[dict] = []
    seen_ids: set[str] = set()
    for pattern in [item for item in raw_memory.get("patterns", []) if isinstance(item, dict)]:
        source = source_by_path.get(str(pattern.get("source_repo_path") or ""))
        source_state = str((source or {}).get("effective_promotion_state") or "")
        source_trust = str((source or {}).get("effective_trust_level") or pattern.get("trust_level") or "trusted")
        override = resolve_pattern_override(controls, pattern)
        if isinstance(override, dict) and override.get("action") == "forget":
            continue
        updated = dict(pattern)
        updated["trust_level"] = source_trust
        updated["promotion_state"] = source_state or ("curated_trusted" if source_trust == "trusted" else "curated_experimental")
        updated["promotion_method"] = str((source or {}).get("promotion_method") or updated.get("promotion_method") or "automatic")
        updated["promotion_reason"] = str((source or {}).get("promotion_reason") or updated.get("promotion_reason") or "")
        updated["timestamp"] = int((source or {}).get("promotion_timestamp") or updated.get("timestamp") or 0)
        updated = apply_manual_metadata(updated, override)
        effective_state = str(updated.get("promotion_state") or "")
        if effective_state == "candidate":
            continue
        seen_ids.add(str(updated.get("id") or ""))
        retained.append(updated)
    for source in effective_sources:
        source_state = str(source.get("effective_promotion_state") or "")
        if source_state == "candidate":
            candidate_rel = str(source.get("candidate_path") or source.get("repo_rel_path") or "").strip()
        else:
            candidate_rel = str(source.get("repo_rel_path") or "").strip()
        if str(source.get("source_kind") or "") == "collection":
            extracted = extract_collection_patterns_from_record(source)
        else:
            if not candidate_rel:
                continue
            candidate_path = repo_root / candidate_rel
            if not candidate_path.exists() or not candidate_path.is_file():
                continue
            extracted = extract_script_patterns_with_metadata(repo_root, candidate_path, source)
        for pattern in extracted:
            override = resolve_pattern_override(controls, pattern)
            if isinstance(override, dict) and override.get("action") == "forget":
                continue
            effective_pattern = dict(pattern)
            effective_pattern["trust_level"] = str(source.get("effective_trust_level") or effective_pattern.get("trust_level") or "trusted")
            effective_pattern["promotion_state"] = source_state
            effective_pattern["promotion_method"] = str(source.get("promotion_method") or "automatic")
            effective_pattern["promotion_reason"] = str(source.get("promotion_reason") or "")
            effective_pattern["timestamp"] = int(source.get("promotion_timestamp") or 0)
            effective_pattern = apply_manual_metadata(effective_pattern, override)
            effective_state = str(effective_pattern.get("promotion_state") or "")
            if effective_state == "candidate":
                continue
            pattern_id = str(effective_pattern.get("id") or "")
            if not pattern_id or pattern_id in seen_ids:
                continue
            seen_ids.add(pattern_id)
            retained.append(effective_pattern)
    return {
        "patterns": sorted(retained, key=lambda item: item.get("id", "")),
        "sources": sorted({str(item.get("source_repo_path") or "") for item in retained if str(item.get("source_repo_path") or "")}),
        "updated_at": int(raw_memory.get("updated_at", 0) or 0),
    }


def load_script_pattern_memory(pattern_repo: Path) -> dict:
    return build_effective_pattern_memory(pattern_repo)


def append_pattern_eval_history(repo: Path, run_result: dict) -> None:
    path = state_storage_path(repo, PATTERN_EVAL_FILE_NAME)
    existing = {"runs": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {"runs": []}
    existing["runs"].append(run_result)
    try:
        path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def append_run_metrics(repo: Path, metrics: dict) -> None:
    path = state_storage_path(repo, METRICS_FILE_NAME)
    existing = {"runs": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {"runs": []}
    existing["runs"].append(metrics)
    try:
        path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def load_json_file(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default
    return loaded if isinstance(loaded, dict) else default


def load_agent_config(explicit_path: str | None, cwd: Path) -> tuple[dict, Path]:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        return load_json_file(path, {}), path
    candidate = cwd / CONFIG_FILE_NAME
    if candidate.exists():
        return load_json_file(candidate, {}), candidate
    fallback = script_state_path(CONFIG_FILE_NAME)
    return load_json_file(fallback, {}), fallback


def resolve_pattern_repo_path(config: dict, cli_value: str | None) -> Path:
    configured = ""
    if isinstance(config, dict):
        configured = str(config.get("pattern_repo", "") or "").strip()
    return normalize_pattern_repo_path(cli_value or configured or default_pattern_repo_path())


def empty_script_pattern_memory() -> dict:
    return {"patterns": [], "sources": [], "updated_at": 0}


def configured_pattern_repo_specs(config: dict | None) -> dict[str, dict]:
    specs: dict[str, dict] = {}
    configured_default = ""
    if isinstance(config, dict):
        configured_default = str(config.get("pattern_repo", "") or "").strip()
    specs["default"] = {
        "name": "default",
        "path": normalize_pattern_repo_path(configured_default or default_pattern_repo_path()),
        "tags": [],
    }
    raw_named = config.get("pattern_repos", {}) if isinstance(config, dict) else {}
    if not isinstance(raw_named, dict):
        return specs
    for raw_name, raw_value in raw_named.items():
        name = str(raw_name or "").strip()
        if not name or name == "default":
            continue
        repo_path = ""
        repo_tags: list[str] = []
        if isinstance(raw_value, str):
            repo_path = raw_value.strip()
        elif isinstance(raw_value, dict):
            repo_path = str(raw_value.get("path", "") or "").strip()
            for tag in raw_value.get("tags", []) if isinstance(raw_value.get("tags"), list) else []:
                candidate = str(tag or "").strip().lower()
                if candidate and candidate not in repo_tags:
                    repo_tags.append(candidate)
        if not repo_path:
            continue
        specs[name] = {
            "name": name,
            "path": normalize_pattern_repo_path(repo_path),
            "tags": repo_tags,
        }
    return specs


def domain_pattern_families(name: str, tags: list[str] | None = None) -> set[str]:
    families: set[str] = set()
    tokens = set(pattern_keywords_from_text(name))
    for tag in tags or []:
        tokens.update(pattern_keywords_from_text(str(tag)))
    for token in tokens:
        families.update(DOMAIN_PATTERN_FAMILIES.get(token, set()))
    return families


def load_pattern_repo_profile(pattern_repo: Path, spec_tags: list[str] | None = None) -> dict:
    resolved = normalize_pattern_repo_path(pattern_repo)
    if not resolved.exists():
        return {
            "path": resolved,
            "curated_sources": [],
            "trusted_count": 0,
            "experimental_count": 0,
            "tags": list(spec_tags or []),
            "terms": set(pattern_keywords_from_text(" ".join(spec_tags or []))),
            "pattern_types": set(),
            "families": set(),
            "effectiveness": load_pattern_effectiveness(resolved),
        }
    catalog = load_pattern_source_catalog(resolved)
    curated_sources = [
        item
        for item in catalog.get("sources", [])
        if isinstance(item, dict) and str(item.get("promotion_state", "")) == "curated"
    ]
    memory = load_script_pattern_memory(resolved)
    tags: list[str] = []
    for tag in spec_tags or []:
        candidate = str(tag or "").strip().lower()
        if candidate and candidate not in tags:
            tags.append(candidate)
    trusted_count = 0
    experimental_count = 0
    terms = set(pattern_keywords_from_text(resolved.name))
    pattern_types: set[str] = set()
    families: set[str] = set()
    for source in curated_sources:
        for tag in source.get("tags", []) if isinstance(source.get("tags"), list) else []:
            candidate = str(tag or "").strip().lower()
            if candidate and candidate not in tags:
                tags.append(candidate)
        terms.update(pattern_keywords_from_text(str(source.get("repo_rel_path", ""))))
        terms.update(pattern_keywords_from_text(str(source.get("origin_path", ""))))
    for pattern in memory.get("patterns", []):
        if not isinstance(pattern, dict):
            continue
        trust_level = str(pattern.get("trust_level", "trusted") or "trusted")
        if trust_level == "trusted":
            trusted_count += 1
        else:
            experimental_count += 1
        pattern_type = str(pattern.get("pattern_type", "") or "")
        family = str(pattern.get("family", pattern_type) or pattern_type)
        if pattern_type:
            pattern_types.add(pattern_type)
            terms.update(pattern_keywords_from_text(pattern_type.replace("_", " ")))
        if family:
            families.add(family)
            terms.update(pattern_keywords_from_text(family.replace("_", " ")))
        for keyword in pattern.get("keywords", []) if isinstance(pattern.get("keywords"), list) else []:
            candidate = str(keyword or "").strip().lower()
            if candidate:
                terms.add(candidate)
    terms.update(pattern_keywords_from_text(" ".join(tags)))
    return {
        "path": resolved,
        "curated_sources": curated_sources,
        "trusted_count": trusted_count,
        "experimental_count": experimental_count,
        "tags": tags,
        "terms": terms,
        "pattern_types": pattern_types,
        "families": families,
        "effectiveness": load_pattern_effectiveness(resolved),
    }


def infer_pattern_repo_selection_context(args: argparse.Namespace, resolved_mode: str) -> tuple[str, str, Path | None]:
    if getattr(args, "new_script", ""):
        output_path = Path(args.new_script)
        return "new-script", str(args.new_script_purpose or output_path.stem), output_path
    if getattr(args, "eval_pattern_learning", False):
        return "eval", str(args.pattern_eval_tasks or "pattern learning eval"), None
    if getattr(args, "script", ""):
        script_path = Path(args.script)
        return "debug", " ".join(part for part in [script_path.stem, getattr(args, "test_cmd", ""), resolved_mode] if part).strip(), script_path
    if getattr(args, "publish_only", False):
        return "publish", "publish current repo state", None
    if getattr(args, "test_cmd", ""):
        return "debug", str(args.test_cmd), None
    return resolved_mode or "debug", resolved_mode or "agent run", None


def select_pattern_repo(
    config: dict | None,
    cli_value: str | None,
    task_type: str,
    task_text: str,
    script_path: Path | None = None,
    require_repo: bool = False,
) -> dict:
    specs = configured_pattern_repo_specs(config)
    raw_override = str(cli_value or "").strip()
    normalized_override = raw_override.lower()
    if normalized_override == "none":
        return {
            "selected": "none",
            "path": None,
            "reason": "operator disabled pattern repo usage",
            "confidence": "high",
            "available": sorted(specs),
            "tags": [],
        }
    if normalized_override and normalized_override != "auto":
        if raw_override in specs:
            spec = specs[raw_override]
            return {
                "selected": raw_override,
                "path": spec["path"],
                "reason": f"operator override selected named pattern repo '{raw_override}'",
                "confidence": "high",
                "available": sorted(specs),
                "tags": list(spec.get("tags", []) or []),
            }
        return {
            "selected": Path(raw_override).expanduser().name or raw_override,
            "path": normalize_pattern_repo_path(raw_override),
            "reason": "operator override selected explicit pattern repo path",
            "confidence": "high",
            "available": sorted(specs),
            "tags": [],
        }
    if require_repo:
        spec = specs.get("default")
        return {
            "selected": "default",
            "path": spec["path"] if spec else default_pattern_repo_path(),
            "reason": "management workflow requires a concrete training repo; using default",
            "confidence": "high",
            "available": sorted(specs),
            "tags": list(spec.get("tags", []) or []) if spec else [],
        }

    query_text = " ".join(part for part in [task_type, task_text, script_path.name if script_path else "", script_path.stem if script_path else ""] if part).strip()
    query_keywords = set(pattern_keywords_from_text(query_text))
    candidates: list[dict] = []
    for name, spec in specs.items():
        profile = load_pattern_repo_profile(spec["path"], spec.get("tags"))
        score = 0.0
        reasons: list[str] = []
        if profile["curated_sources"]:
            score += 1.0
            reasons.append("has curated sources")
        if profile["trusted_count"]:
            score += min(2.5, 0.25 * float(profile["trusted_count"]))
            reasons.append("trusted curated patterns available")
        if not profile["trusted_count"] and profile["experimental_count"]:
            score -= 1.0
            reasons.append("only experimental patterns available")
        overlap = sorted(query_keywords & set(profile["terms"]))
        if overlap:
            score += min(5.0, float(len(overlap)) * 1.5)
            reasons.append("matched " + ", ".join(overlap[:4]))
        if name != "default" and name.lower() in query_keywords:
            score += 2.5
            reasons.append(f"repo name matched task domain '{name}'")
        success_rate, overapply_rate, avg_delta = effectiveness_stats_view(profile.get("effectiveness", {}).get("repos", {}).get(name, {}), task_type)
        if success_rate:
            score += min(1.5, success_rate * 1.5)
            reasons.append(f"repo effectiveness success_rate={success_rate:.2f}")
        if avg_delta:
            score += max(-0.75, min(0.75, avg_delta / 20.0))
        if overapply_rate:
            score -= min(1.5, overapply_rate * 1.5)
            reasons.append(f"repo overapplication penalty={overapply_rate:.2f}")
        if not overlap and not profile["curated_sources"]:
            reasons.append("no curated matches")
        candidates.append(
            {
                "name": name,
                "path": spec["path"],
                "score": score,
                "reasons": reasons,
                "profile": profile,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], 0 if item["name"] != "default" else 1, item["name"]))
    best = candidates[0] if candidates else None
    default_candidate = next((item for item in candidates if item["name"] == "default"), None)
    if best and best["profile"]["curated_sources"] and best["score"] >= 3.0:
        confidence = "high" if best["score"] >= 5.0 else "medium"
        return {
            "selected": best["name"],
            "path": best["path"],
            "reason": "; ".join(best["reasons"][:3]) or "highest relevance score among available repos",
            "confidence": confidence,
            "available": sorted(specs),
            "tags": list(specs.get(best["name"], {}).get("tags", []) or []),
        }
    if default_candidate and default_candidate["profile"]["curated_sources"] and default_candidate["score"] >= 2.0:
        confidence = "medium" if default_candidate["score"] >= 3.5 else "low"
        return {
            "selected": "default",
            "path": default_candidate["path"],
            "reason": "; ".join(default_candidate["reasons"][:3]) or "default repo had the best available relevance",
            "confidence": confidence,
            "available": sorted(specs),
            "tags": list(specs.get("default", {}).get("tags", []) or []),
        }
    return {
        "selected": "none",
        "path": None,
        "reason": "no relevant curated training repo was confident enough for this task",
        "confidence": "low",
        "available": sorted(specs),
        "tags": [],
    }


def cli_option_value(argv: list[str], option_name: str) -> str | None:
    for index, item in enumerate(argv):
        if item == option_name and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(option_name + "="):
            return item.split("=", 1)[1]
    return None


def load_recent_state() -> dict:
    return load_json_file(script_state_path(RECENT_STATE_FILE_NAME), {"recent_runs": []})


def save_recent_state(state: dict) -> None:
    path = script_state_path(RECENT_STATE_FILE_NAME)
    try:
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def update_recent_state(
    repo: Path,
    test_cmd: str,
    mode: str,
    success: bool | str,
    artifact_dir: Path | None = None,
    target: str = "",
    files_changed: list[str] | None = None,
    confidence: str = "",
    blocked_reason: str = "",
) -> Path:
    validation_result = str(success).strip() if isinstance(success, str) else ("success" if success else "failed")
    commit_hash = parse_head_commit(repo) if is_git_repo(repo) else ""
    meaningful_content_fingerprint = current_validation_fingerprint(repo) if is_git_repo(repo) else ""
    state = load_recent_state()
    runs = [item for item in state.get("recent_runs", []) if isinstance(item, dict)]
    runs.append(
        {
            "repo": str(repo),
            "test_cmd": test_cmd,
            "mode": mode,
            "success": validation_result == "success",
            "validation_result": validation_result,
            "validation_command": test_cmd,
            "commit_hash": commit_hash,
            "artifact_dir": str(artifact_dir) if artifact_dir else "",
            "target": target,
            "files_changed": list(files_changed or []),
            "confidence": confidence,
            "blocked_reason": blocked_reason,
            "meaningful_content_fingerprint": meaningful_content_fingerprint,
            "ts": int(time.time()),
        }
    )
    state["recent_runs"] = runs[-10:]
    save_recent_state(state)
    return script_state_path(RECENT_STATE_FILE_NAME)


def resolve_publish_validation_state(repo: Path) -> dict:
    state = load_recent_state()
    runs = [item for item in state.get("recent_runs", []) if isinstance(item, dict)]
    repo_runs = [item for item in reversed(runs) if item.get("repo") == str(repo) and not item.get("target")]
    current_commit = parse_head_commit(repo) if is_git_repo(repo) else ""
    publish_changes = classify_publishable_changes(repo) if is_git_repo(repo) else {
        "meaningful_changes_detected": False,
        "meaningful_paths": [],
        "ignored_changes": [],
    }
    current_fingerprint = compute_meaningful_content_fingerprint(repo, publish_changes) if is_git_repo(repo) else ""
    if not repo_runs:
        return {
            "validation_state": "blocked",
            "validation_result": "blocked",
            "validation_commit_match": False,
            "fingerprint_match": False,
            "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
            "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
            "ignored_changes": list(publish_changes.get("ignored_changes") or []),
            "last_validated_fingerprint": "",
            "current_fingerprint": current_fingerprint,
            "last_validated_commit": "",
            "current_commit": current_commit,
            "validation_age_seconds": -1,
            "reason": "publish blocked because no validation record was recorded for this repo; use --force-publish to override",
        }
    last = repo_runs[0]
    last_commit = str(last.get("commit_hash") or "").strip()
    last_fingerprint = str(last.get("meaningful_content_fingerprint") or "").strip()
    validation_result = str(last.get("validation_result") or ("success" if last.get("success") else "failed")).strip() or "failed"
    validation_age_seconds = -1
    try:
        ts = int(last.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts > 0:
        validation_age_seconds = max(0, int(time.time()) - ts)
    commit_match = bool(current_commit and last_commit and current_commit == last_commit)
    fingerprint_match = bool(last_fingerprint and current_fingerprint and last_fingerprint == current_fingerprint)
    blocked_reason = str(last.get("blocked_reason") or "").strip()
    if not last_commit:
        return {
            "validation_state": "blocked",
            "validation_result": "blocked",
            "validation_commit_match": False,
            "fingerprint_match": False,
            "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
            "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
            "ignored_changes": list(publish_changes.get("ignored_changes") or []),
            "last_validated_fingerprint": last_fingerprint,
            "current_fingerprint": current_fingerprint,
            "last_validated_commit": "",
            "current_commit": current_commit,
            "validation_age_seconds": validation_age_seconds,
            "reason": "publish blocked because the last validation record did not capture a validated commit; use --force-publish to override",
        }
    if not commit_match:
        if validation_result == "success" and fingerprint_match:
            return {
                "validation_state": "success",
                "validation_result": "success",
                "validation_commit_match": False,
                "fingerprint_match": True,
                "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
                "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
                "ignored_changes": list(publish_changes.get("ignored_changes") or []),
                "last_validated_fingerprint": last_fingerprint,
                "current_fingerprint": current_fingerprint,
                "last_validated_commit": last_commit,
                "current_commit": current_commit,
                "validation_age_seconds": validation_age_seconds,
                "reason": "validated_reused_fingerprint",
            }
        return {
            "validation_state": "blocked",
            "validation_result": "blocked",
            "validation_commit_match": False,
            "fingerprint_match": fingerprint_match,
            "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
            "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
            "ignored_changes": list(publish_changes.get("ignored_changes") or []),
            "last_validated_fingerprint": last_fingerprint,
            "current_fingerprint": current_fingerprint,
            "last_validated_commit": last_commit,
            "current_commit": current_commit,
            "validation_age_seconds": validation_age_seconds,
            "reason": (
                f"publish blocked because current commit {current_commit or '(none)'} does not match "
                f"last validated commit {last_commit}; use --force-publish to override"
            ),
        }
    if validation_result == "success":
        return {
            "validation_state": "success",
            "validation_result": "success",
            "validation_commit_match": True,
            "fingerprint_match": fingerprint_match,
            "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
            "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
            "ignored_changes": list(publish_changes.get("ignored_changes") or []),
            "last_validated_fingerprint": last_fingerprint,
            "current_fingerprint": current_fingerprint,
            "last_validated_commit": last_commit,
            "current_commit": current_commit,
            "validation_age_seconds": validation_age_seconds,
            "reason": "validated",
        }
    if validation_result == "blocked":
        return {
            "validation_state": "blocked",
            "validation_result": "blocked",
            "validation_commit_match": True,
            "fingerprint_match": fingerprint_match,
            "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
            "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
            "ignored_changes": list(publish_changes.get("ignored_changes") or []),
            "last_validated_fingerprint": last_fingerprint,
            "current_fingerprint": current_fingerprint,
            "last_validated_commit": last_commit,
            "current_commit": current_commit,
            "validation_age_seconds": validation_age_seconds,
            "reason": (
                f"publish blocked because the latest validation run was blocked: {blocked_reason or 'validation did not complete'}; "
                "use --force-publish to override"
            ),
        }
    return {
        "validation_state": "failed",
        "validation_result": "failed",
        "validation_commit_match": True,
        "fingerprint_match": fingerprint_match,
        "meaningful_changes_detected": bool(publish_changes.get("meaningful_changes_detected")),
        "meaningful_paths": list(publish_changes.get("meaningful_paths") or []),
        "ignored_changes": list(publish_changes.get("ignored_changes") or []),
        "last_validated_fingerprint": last_fingerprint,
        "current_fingerprint": current_fingerprint,
        "last_validated_commit": last_commit,
        "current_commit": current_commit,
        "validation_age_seconds": validation_age_seconds,
        "reason": "publish blocked because the latest validation run failed; use --force-publish to override",
    }


def ensure_validation_record_for_current_commit(
    repo: Path,
    *,
    validation_command: str = "",
    target: str = "",
) -> dict:
    state = resolve_publish_validation_state(repo)
    current_commit = str(state.get("current_commit") or parse_head_commit(repo) or "").strip()
    last_commit = str(state.get("last_validated_commit") or "").strip()
    validation_result = str(state.get("validation_result") or state.get("validation_state") or "blocked").strip() or "blocked"
    if current_commit and last_commit == current_commit and validation_result == "success":
        return {
            "ok": True,
            "validation_record_created": False,
            "validation_record_reused": True,
            "validation_commit": current_commit,
            "validation_result": validation_result,
            "validation_command": str(validation_command or latest_repo_validation_command(repo) or ""),
            "reason": str(state.get("reason") or ""),
            "validation_error_type": "unknown",
            "fallback_validation_used": False,
            "fallback_validation_result": "not_needed",
        }
    command = str(validation_command or latest_repo_validation_command(repo) or "").strip()
    if not command:
        return {
            "ok": False,
            "validation_record_created": False,
            "validation_record_reused": False,
            "validation_commit": current_commit,
            "validation_result": validation_result if current_commit and last_commit == current_commit else "blocked",
            "validation_command": "",
            "reason": (
                f"no validation command is available to refresh the current commit validation record (last result: {validation_result})"
                if current_commit and last_commit == current_commit
                else "no validation command is available to create a validation record for the current commit"
            ),
            "validation_error_type": "unknown",
            "fallback_validation_used": False,
            "fallback_validation_result": "not_needed",
        }
    validation_run = run_repo_validation_command(
        repo,
        command,
        mode="finalization-prepare",
        confidence="finalization-prepare",
        target=target,
    )
    refreshed = resolve_publish_validation_state(repo)
    refreshed_result = str(refreshed.get("validation_result") or refreshed.get("validation_state") or "blocked").strip() or "blocked"
    return {
        "ok": validation_run.get("ok") and refreshed_result == "success",
        "validation_record_created": True,
        "validation_record_reused": False,
        "validation_commit": str(refreshed.get("current_commit") or current_commit or ""),
        "validation_result": refreshed_result,
        "validation_command": str(validation_run.get("validation_command_used") or command),
        "reason": str(refreshed.get("reason") or validation_run.get("reason") or ""),
        "validation_error_type": str(validation_run.get("validation_error_type") or "unknown"),
        "fallback_validation_used": bool(validation_run.get("fallback_validation_used")),
        "fallback_validation_result": str(validation_run.get("fallback_validation_result") or "not_needed"),
    }


def print_validation_record_result(result: dict) -> None:
    print("\n=== VALIDATION RECORD ===")
    print(f"validation_record_created: {format_bool(result.get('validation_record_created'))}")
    print(f"validation_record_reused: {format_bool(result.get('validation_record_reused'))}")
    print(f"validation_commit: {result.get('validation_commit') or '(none)'}")
    print(f"validation_result: {result.get('validation_result') or 'blocked'}")
    if result.get("validation_command"):
        print(f"validation_command: {result.get('validation_command')}")
    print(f"validation_error_type: {result.get('validation_error_type') or 'unknown'}")
    print(f"fallback_validation_used: {format_bool(result.get('fallback_validation_used'))}")
    print(f"fallback_validation_result: {result.get('fallback_validation_result') or 'not_needed'}")
    if result.get("fallback_validation_used") and result.get("fallback_validation_result") == "passed":
        print("what_happened: Validation command failed; used fallback validation.")
        print("what_happened_detail: Fallback validation passed; continuing publish.")
    if result.get("reason"):
        print(f"validation_record_reason: {result.get('reason')}")


def attempt_publish_auto_revalidation(
    repo: Path,
    validation_state: dict,
    *,
    no_auto_revalidate: bool = False,
) -> dict:
    result = dict(validation_state)
    original_commit_match = bool(result.get("validation_commit_match"))
    fingerprint_match = bool(result.get("fingerprint_match"))
    validation_stale = bool(result.get("validation_state") == "success" and not original_commit_match)
    pre_rerun_last_validated_commit = str(result.get("last_validated_commit") or "")
    result["auto_revalidated"] = False
    result["validation_reused"] = bool(
        result.get("validation_state") == "success"
        and (result.get("validation_commit_match") or result.get("fingerprint_match"))
    )
    result["auto_revalidation_result"] = "not_needed"
    result["auto_revalidation_attempted"] = False
    result["validation_stale_detected"] = validation_stale
    result["validation_rerun_attempted"] = False
    result["validation_rerun_result"] = "not_needed"
    result["validation_commit_updated"] = False
    if result.get("validation_state") == "success" and original_commit_match:
        result["publish_reason"] = "validated"
        return result
    if result.get("validation_state") == "success" and fingerprint_match:
        result["validation_reused"] = True
        result["publish_reason"] = "validated_reused_fingerprint"
        result["reason"] = "validated_reused_fingerprint"
        return result
    if not bool(result.get("meaningful_changes_detected")) and str(result.get("validation_result") or "") == "blocked" and not original_commit_match:
        result["validation_state"] = "success"
        result["validation_result"] = "success"
        result["validation_reused"] = True
        result["publish_reason"] = "validated_reused_noop"
        result["reason"] = "validated_reused_noop"
        return result
    if no_auto_revalidate:
        result["validation_reused"] = False
        return result
    state = load_recent_state()
    runs = [item for item in state.get("recent_runs", []) if isinstance(item, dict)]
    repo_runs = [item for item in reversed(runs) if item.get("repo") == str(repo) and not item.get("target")]
    if not repo_runs:
        result["validation_reused"] = False
        return result
    last = repo_runs[0]
    validation_command = str(last.get("validation_command") or last.get("test_cmd") or "").strip()
    if not validation_command:
        result["validation_reused"] = False
        result["auto_revalidation_result"] = "blocked"
        result["reason"] = "publish blocked because no validation command was recorded for auto-revalidation; use --force-publish to override"
        return result
    result["auto_revalidation_attempted"] = True
    code, output = run_subprocess(validation_command, repo, shell=True)
    blocked_reason = ""
    validation_result = "success" if code == 0 else "failed"
    if code != 0:
        blocked_reason = (output or "").strip()[:500]
    update_recent_state(
        repo,
        validation_command,
        "publish-auto-revalidate",
        validation_result,
        None,
        "",
        files_changed=[],
        confidence="auto-revalidate",
        blocked_reason=blocked_reason,
    )
    refreshed = resolve_publish_validation_state(repo)
    refreshed["auto_revalidated"] = True
    refreshed["validation_reused"] = False
    refreshed["auto_revalidation_attempted"] = True
    post_revalidation_commit_match = bool(refreshed.get("validation_commit_match"))
    refreshed["validation_commit_match"] = original_commit_match
    refreshed["auto_revalidation_result"] = "success" if refreshed.get("validation_state") == "success" and post_revalidation_commit_match else "failed"
    refreshed["validation_stale_detected"] = validation_stale
    refreshed["validation_rerun_attempted"] = True
    refreshed["validation_rerun_result"] = "success" if refreshed.get("auto_revalidation_result") == "success" else "failed"
    refreshed["validation_commit_updated"] = bool(
        refreshed.get("last_validated_commit")
        and refreshed.get("last_validated_commit") != pre_rerun_last_validated_commit
    )
    if not post_revalidation_commit_match:
        refreshed["validation_state"] = "blocked"
        refreshed["validation_result"] = "blocked"
        refreshed["auto_revalidation_result"] = "blocked"
        refreshed["reason"] = (
            f"publish blocked because current commit {refreshed.get('current_commit') or '(none)'} changed again after the auto-revalidation attempt; "
            "use --force-publish or rerun publish"
        )
        return refreshed
    if refreshed.get("validation_state") != "success":
        refreshed["reason"] = (
            f"publish blocked because auto-revalidation failed for current commit {refreshed.get('current_commit') or '(none)'}; "
            "use --force-publish to override"
        )
    else:
        refreshed["publish_reason"] = "validated_after_revalidation"
    return refreshed


def load_publish_state(repo: Path) -> dict:
    path = state_storage_path(repo, PUBLISH_STATE_FILE_NAME)
    default = {
        "last_target": "",
        "last_repo": "",
        "last_transport": "",
        "fork_created": False,
        "fork_repo": "",
        "last_success": False,
        "timestamp": 0,
        "origin_url": "",
        "ssh_confirmed": False,
        "last_branch": "",
        "last_commit": "",
        "last_pr_url": "",
        "last_success_timestamp": 0,
        "last_publish_mode": "",
        "last_meaningful_paths": [],
        "last_meaningful_content_fingerprint": "",
        "last_target_repo": "",
        "last_control_path": "",
    }
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text())
    except Exception:
        return default
    if not isinstance(loaded, dict):
        return default
    default.update({k: loaded.get(k, v) for k, v in default.items()})
    return default


def save_publish_state(repo: Path, state: dict) -> None:
    path = state_storage_path(repo, PUBLISH_STATE_FILE_NAME)
    payload = {
        "last_target": state.get("last_target", ""),
        "last_repo": state.get("last_repo", ""),
        "last_transport": state.get("last_transport", ""),
        "fork_created": bool(state.get("fork_created")),
        "fork_repo": state.get("fork_repo", ""),
        "last_success": bool(state.get("last_success")),
        "timestamp": int(state.get("timestamp", int(time.time())) or int(time.time())),
        "origin_url": state.get("origin_url", ""),
        "ssh_confirmed": bool(state.get("ssh_confirmed")),
        "last_branch": state.get("last_branch", ""),
        "last_commit": state.get("last_commit", ""),
        "last_pr_url": state.get("last_pr_url", ""),
        "last_success_timestamp": int(state.get("last_success_timestamp", 0) or 0),
        "last_publish_mode": state.get("last_publish_mode", ""),
        "last_meaningful_paths": list(state.get("last_meaningful_paths", []) or []),
        "last_meaningful_content_fingerprint": state.get("last_meaningful_content_fingerprint", ""),
        "last_target_repo": state.get("last_target_repo", ""),
        "last_control_path": state.get("last_control_path", ""),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def infer_mode(test_cmd: str, recent_runs: list[dict], repo: Path) -> tuple[str, str]:
    same_repo_runs = [item for item in recent_runs if item.get("repo") == str(repo)]
    last_same_repo = same_repo_runs[-1] if same_repo_runs else {}
    if last_same_repo.get("success") is False:
        return "deep", "inferred from recent failed run in the same repo"
    lowered = test_cmd.lower()
    if "::" in test_cmd or "tests/" in lowered or "test_" in lowered:
        return "quick", "inferred from narrow test target"
    return "safe", "inferred from broader or unknown scope"


def missing_test_command_message(repo: Path | None) -> str:
    repo_part = f' --repo "{repo}"' if repo else ""
    return (
        "BLOCKED: no reproducible failing test command.\n"
        "Evidence: no explicit test command, no reusable recent failure, and no config default.\n"
        "Need: a concrete command that reproduces the failure.\n"
        "Suggested action:\n"
        f'Try: python {Path(__file__).resolve()}{repo_part} "pytest tests/test_x.py -q"\n'
        "You can also use --last, --continue, or --reuse-last-test once you have a prior run."
    )


def detect_repo_for_script(script_path: Path) -> Path:
    detected = detect_current_repo(script_path.parent.resolve())
    return detected.resolve() if detected else script_path.parent.resolve()


def read_text_limited(path: Path, max_chars: int = 16000) -> str:
    try:
        return path.read_text()[:max_chars]
    except OSError:
        return ""


def relative_script_path(repo: Path, script_path: Path) -> str:
    try:
        return str(script_path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return script_path.name


def infer_module_name(repo: Path, script_path: Path) -> str:
    try:
        rel = script_path.resolve().relative_to(repo.resolve())
    except ValueError:
        return ""
    if rel.suffix != ".py":
        return ""
    if any(not part.replace("_", "").isalnum() for part in rel.with_suffix("").parts):
        return ""
    return ".".join(rel.with_suffix("").parts)


def has_main_guard(node: ast.Module) -> bool:
    for stmt in node.body:
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        if not isinstance(test, ast.Compare):
            continue
        if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
            continue
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue
        if len(test.comparators) != 1:
            continue
        comparator = test.comparators[0]
        if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
            return True
    return False


def extract_script_features(script_path: Path) -> dict:
    text = read_text_limited(script_path)
    if not text.strip():
        return {
            "text": "",
            "tree": None,
            "has_main_guard": False,
            "uses_argparse": False,
            "uses_click": False,
            "uses_typer": False,
            "entrypoints": [],
            "top_level_calls": False,
            "functions": [],
            "url_literals": [],
            "mentions_m3u8": False,
            "uses_network_client": False,
        }
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {
            "text": text,
            "tree": None,
            "has_main_guard": "__main__" in text,
            "uses_argparse": "argparse" in text,
            "uses_click": "click" in text,
            "uses_typer": "typer" in text,
            "entrypoints": [],
            "top_level_calls": False,
            "functions": [],
            "url_literals": re.findall(r"https?://[^\s\"'<>]+", text)[:6],
            "mentions_m3u8": ".m3u8" in text.lower() or "#extm3u" in text.lower(),
            "uses_network_client": any(token in text.lower() for token in ["requests.", "httpx.", "urllib.request", "aiohttp", "urlopen("]),
        }

    imported_names = set()
    entrypoints: list[str] = []
    functions: list[ast.FunctionDef] = []
    top_level_calls = False
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                imported_names.add(alias.name.split(".")[0])
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            imported_names.add(stmt.module.split(".")[0])
        elif isinstance(stmt, ast.FunctionDef):
            functions.append(stmt)
            if stmt.name in {"main", "run", "cli"}:
                entrypoints.append(stmt.name)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            top_level_calls = True
        elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            top_level_calls = True

    lowered = text.lower()
    url_literals = re.findall(r"https?://[^\s\"'<>]+", text)[:6]
    uses_network_client = any(name in imported_names for name in {"requests", "httpx", "urllib", "aiohttp"}) or any(
        token in lowered for token in ["requests.", "httpx.", "urllib.request", "urlopen(", "build_opener("]
    )
    return {
        "text": text,
        "tree": tree,
        "has_main_guard": has_main_guard(tree),
        "uses_argparse": "argparse" in imported_names or "argparse." in text,
        "uses_click": "click" in imported_names or "@click.command" in lowered or "click.command" in lowered,
        "uses_typer": "typer" in imported_names or "typer.Typer" in text or "typer()" in lowered,
        "entrypoints": sorted(set(entrypoints)),
        "top_level_calls": top_level_calls,
        "functions": functions,
        "url_literals": url_literals,
        "mentions_m3u8": ".m3u8" in lowered or "#extm3u" in lowered,
        "uses_network_client": uses_network_client,
    }


def find_nearby_pytest_targets(repo: Path, script_path: Path) -> list[dict]:
    script_stem = script_path.stem
    candidates: list[dict] = []
    patterns = [
        repo / "tests" / f"test_{script_stem}.py",
        repo / f"test_{script_stem}.py",
        script_path.parent / f"test_{script_stem}.py",
        script_path.parent / "tests" / f"test_{script_stem}.py",
    ]
    seen: set[str] = set()
    for path in patterns:
        if not path.exists() or not path.is_file():
            continue
        rel = str(path.resolve().relative_to(repo.resolve())) if path.is_relative_to(repo.resolve()) else path.name
        if rel in seen:
            continue
        seen.add(rel)
        candidates.append(
            {
                "command": f"pytest {shlex.quote(rel)} -q",
                "kind": "pytest",
                "confidence": 0.95,
                "reason": f"Nearby pytest target matches script stem `{script_stem}`.",
                "source": rel,
            }
        )
    return candidates


def recommend_script_probe_targets(features: dict) -> list[dict]:
    urls = [str(item).strip() for item in (features.get("url_literals") or []) if str(item).strip()]
    recommendations: list[dict] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        probe_type = "m3u8_summary" if url.lower().endswith(".m3u8") or features.get("mentions_m3u8") else "json_summary"
        reason = (
            "script references an HLS playlist and live playlist structure may affect parsing or validation"
            if probe_type == "m3u8_summary"
            else "script references a live HTTP endpoint and response shape may affect parsing or auth handling"
        )
        recommendations.append(
            {
                "endpoint": redact_probe_url(url)[0],
                "probe_type": probe_type,
                "reason": reason,
            }
        )
    if not recommendations and features.get("uses_network_client"):
        recommendations.append(
            {
                "endpoint": "",
                "probe_type": "headers_summary",
                "reason": "script appears network-dependent; probe the live endpoint before changing parsing, auth, or proxy logic",
            }
        )
    return recommendations[:3]


def extract_probe_header_candidates(text: str) -> dict[str, str]:
    header_candidates: dict[str, str] = {}
    patterns = [
        "Accept",
        "Authorization",
        "Cookie",
        "Origin",
        "Referer",
        "User-Agent",
        "X-API-Key",
        "X-Api-Key",
    ]
    for header in patterns:
        match = re.search(rf'["\']{re.escape(header)}["\']\s*:\s*["\']([^"\n]{{1,200}})["\']', str(text or ""))
        if match:
            header_candidates[header] = match.group(1)
    return header_candidates


def classify_network_dependency(features: dict) -> dict:
    url_literals = [str(item).strip() for item in (features.get("url_literals") or []) if str(item).strip()]
    mentions_m3u8 = bool(features.get("mentions_m3u8"))
    uses_network_client = bool(features.get("uses_network_client"))
    if mentions_m3u8 and url_literals:
        return {"detected": True, "confidence": "high", "reason": "script references a concrete HLS playlist URL"}
    if uses_network_client and url_literals:
        return {"detected": True, "confidence": "high", "reason": "script uses an HTTP client and embeds a concrete live endpoint"}
    if mentions_m3u8 or uses_network_client:
        return {"detected": True, "confidence": "medium", "reason": "script appears network-dependent but does not expose one clear endpoint"}
    return {"detected": False, "confidence": "low", "reason": ""}


def prefer_safer_validation_step(plan: dict) -> dict | None:
    current_primary = str(plan.get("primary_command") or "")
    fallback_order = ["import", "cli_help", "module_help"]
    ranked = [item for item in plan.get("candidates", []) if isinstance(item, dict)]
    for kind in fallback_order:
        for candidate in ranked:
            if candidate.get("kind") != kind:
                continue
            if candidate.get("command") == current_primary and kind != "import":
                continue
            return candidate
    return None


def apply_probe_findings_to_validation_plan(plan: dict, probe_findings: list[dict]) -> dict:
    enriched = dict(plan)
    findings = [dict(item) for item in probe_findings if isinstance(item, dict)]
    enriched["probe_findings"] = findings
    enriched["auto_probe_used"] = bool(findings)
    enriched["probe_reasoning"] = ""
    if not findings:
        return enriched
    primary_finding = findings[0]
    endpoint = str(primary_finding.get("endpoint") or "(unknown endpoint)")
    summary = str(primary_finding.get("summary") or primary_finding.get("error") or "")
    if not primary_finding.get("ok") or primary_finding.get("status_code") in {401, 403, 407}:
        safer = prefer_safer_validation_step(enriched)
        if safer:
            enriched["chosen_stack"] = [enriched.get("chosen_stack", [])[0], safer] if enriched.get("chosen_stack") else [safer]
            enriched["primary_command"] = safer.get("command", enriched.get("primary_command", ""))
            enriched["limited_validation"] = True
            enriched["only_syntax_import_validation"] = all(
                step.get("kind") in {"syntax", "import", "cli_help", "module_help"}
                for step in enriched.get("chosen_stack", [])
            )
            enriched["confidence_level"] = "low"
            enriched["limited_reason"] = (
                "Live probe indicates the endpoint is auth/proxy constrained or unreachable; "
                "runtime validation was downgraded to a safer non-network path."
            )
            enriched["probe_reasoning"] = f"Used live probe evidence from {endpoint} to choose a safer validation path: {summary}"
            return enriched
    if primary_finding.get("ok"):
        enriched["probe_reasoning"] = f"Used live probe evidence from {endpoint} to confirm endpoint behavior before repair: {summary}"
    else:
        enriched["probe_reasoning"] = f"Attempted live probe for {endpoint} but it remained inconclusive: {summary}"
    return enriched


def maybe_enrich_validation_plan_with_probes(plan: dict) -> dict:
    if not isinstance(plan, dict) or not plan.get("active"):
        return plan
    if plan.get("auto_probe_evaluated"):
        return plan
    enriched = dict(plan)
    enriched["auto_probe_evaluated"] = True
    dependency = enriched.get("network_dependency") or {}
    recommendations = [item for item in enriched.get("probe_recommendations", []) if isinstance(item, dict)]
    if not dependency.get("detected") or dependency.get("confidence") != "high" or not recommendations:
        enriched.setdefault("auto_probe_used", False)
        enriched.setdefault("probe_findings", [])
        enriched.setdefault("probe_reasoning", "")
        return enriched
    endpoint = str(recommendations[0].get("endpoint") or "").strip()
    if not endpoint:
        enriched.setdefault("auto_probe_used", False)
        enriched.setdefault("probe_findings", [])
        enriched.setdefault("probe_reasoning", "")
        return enriched
    probe_kwargs = {
        "probe_type": str(recommendations[0].get("probe_type") or "auto"),
        "custom_headers": {},
        "http_proxy": "",
        "https_proxy": "",
    }
    if sys.stdin.isatty():
        if not prompt_yes_no("Probe endpoint now to validate live API/M3U8 behavior?", default=True):
            enriched.setdefault("auto_probe_used", False)
            enriched.setdefault("probe_findings", [])
            enriched["probe_reasoning"] = "Interactive operator declined the suggested live probe."
            return enriched
        default_probe_type = "m3u8" if "m3u8" in probe_kwargs["probe_type"] else "api"
        endpoint_choice = input(f"Endpoint type [api/m3u8] ({default_probe_type}): ").strip().lower() or default_probe_type
        if endpoint_choice == "m3u8":
            probe_kwargs["probe_type"] = "m3u8_summary"
        elif endpoint_choice == "api":
            probe_kwargs["probe_type"] = "json_summary"
        if not prompt_yes_no(
            f"Use proxy settings for the probe? [{'yes' if API_SAFETY_STATE.get('proxy_enabled') else 'no'}]",
            default=bool(API_SAFETY_STATE.get("proxy_enabled")),
        ):
            probe_kwargs["http_proxy"] = ""
            probe_kwargs["https_proxy"] = ""
        else:
            probe_kwargs["http_proxy"] = API_SAFETY_STATE.get("http_proxy", "")
            probe_kwargs["https_proxy"] = API_SAFETY_STATE.get("https_proxy", "")
        if prompt_yes_no("Include headers detected from the script in the probe request?", default=False):
            probe_kwargs["custom_headers"] = enriched.get("suggested_probe_headers") or {}
    probe = probe_endpoint(endpoint, **probe_kwargs)
    return apply_probe_findings_to_validation_plan(enriched, [probe])


def discover_context_candidates(repo: Path, script_rel: str, module_name: str, script_stem: str) -> list[dict]:
    candidates: list[dict] = []
    tokens = [script_rel, script_stem]
    if module_name:
        tokens.append(module_name)
    context_files = [
        repo / "README.md",
        repo / "docs" / "README.md",
        repo / "Makefile",
        repo / "pyproject.toml",
        repo / "tox.ini",
        repo / "noxfile.py",
    ]
    workflows = repo / ".github" / "workflows"
    if workflows.exists():
        for path in sorted(workflows.glob("*.y*ml")):
            context_files.append(path)

    seen_commands: set[str] = set()
    for path in context_files:
        if not path.exists() or not path.is_file():
            continue
        text = read_text_limited(path, max_chars=12000)
        lowered = text.lower()
        if not any(token.lower() in lowered for token in tokens if token):
            continue
        rel = str(path.resolve().relative_to(repo.resolve())) if path.is_relative_to(repo.resolve()) else path.name
        for line in text.splitlines():
            stripped = line.strip()
            lowered_line = stripped.lower()
            if not any(token.lower() in lowered_line for token in tokens if token):
                continue
            if "pytest" in lowered_line:
                command = "pytest -q"
            elif "python -m" in lowered_line and "--help" in lowered_line:
                match = re.search(r"python -m ([A-Za-z0-9_\.]+).*--help", stripped)
                command = f"python -m {match.group(1)} --help" if match else ""
            elif "python " in lowered_line and "--help" in lowered_line and script_stem in lowered_line:
                command = f"python {shlex.quote(script_rel)} --help"
            else:
                command = ""
            if command and command not in seen_commands:
                seen_commands.add(command)
                candidates.append(
                    {
                        "command": command,
                        "kind": "context",
                        "confidence": 0.65,
                        "reason": f"Repository docs/config mention a related command in `{rel}`.",
                        "source": rel,
                    }
                )
    return candidates


PURE_FUNCTION_NAME_HINTS = {
    "add",
    "build",
    "calc",
    "clean",
    "combine",
    "compute",
    "format",
    "join",
    "merge",
    "normalize",
    "parse",
    "slugify",
    "split",
    "strip",
    "sum",
}
SIDE_EFFECT_NAME_TOKENS = {
    "connect",
    "db",
    "delete",
    "download",
    "fetch",
    "file",
    "http",
    "insert",
    "load",
    "network",
    "open",
    "path",
    "post",
    "put",
    "read",
    "request",
    "save",
    "send",
    "socket",
    "sql",
    "subprocess",
    "update",
    "upload",
    "url",
    "write",
}
IMPURE_CALL_NAMES = {
    "open",
    "print",
    "input",
    "exec",
    "eval",
    "system",
    "popen",
    "run",
    "request",
    "get",
    "post",
    "connect",
    "read_text",
    "write_text",
    "unlink",
    "mkdir",
}


def callable_name(expr: ast.AST) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = callable_name(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    return ""


def infer_sample_value(arg_name: str, annotation: ast.AST | None) -> object | None:
    normalized = arg_name.lower()
    annotation_name = callable_name(annotation) if annotation else ""
    if any(token in normalized for token in SIDE_EFFECT_NAME_TOKENS):
        return None
    if annotation_name in {"int", "float"} or normalized in {"a", "b", "x", "y", "n"}:
        return 3
    if annotation_name == "bool":
        return True
    if annotation_name in {"list", "tuple"} or normalized.endswith("s") or normalized in {"items", "values"}:
        return [1, 2, 3]
    if annotation_name in {"dict", "mapping"}:
        return {"key": "value"}
    if annotation_name == "str" or any(token in normalized for token in ["text", "name", "value", "word", "slug"]):
        return "Hello World"
    if normalized in {"sep", "delimiter"}:
        return "-"
    return None


def function_has_obvious_side_effects(node: ast.FunctionDef) -> bool:
    lowered_name = node.name.lower()
    if any(token in lowered_name for token in SIDE_EFFECT_NAME_TOKENS):
        return True
    banned_nodes = (ast.With, ast.AsyncWith, ast.Try, ast.Raise, ast.Yield, ast.YieldFrom, ast.Await, ast.Global, ast.Nonlocal)
    for child in ast.walk(node):
        if isinstance(child, banned_nodes):
            return True
        if isinstance(child, ast.Call):
            name = callable_name(child.func).lower()
            if any(part in name for part in IMPURE_CALL_NAMES):
                return True
    return False


def module_safe_to_import_for_function_checks(features: dict) -> bool:
    return bool(features.get("tree")) and not features.get("top_level_calls")


def discover_function_validation(script_path: Path, script_rel: str, features: dict) -> dict:
    result = {
        "considered": False,
        "used": False,
        "confidence": "low",
        "reason": "No high-confidence pure helper functions found.",
        "functions": [],
        "step": None,
    }
    if not module_safe_to_import_for_function_checks(features):
        result["reason"] = "Skipped function-level validation because the module may execute code on import."
        return result

    for func in features.get("functions", []):
        if func.name.startswith("__") or func.name in {"main", "run", "cli"}:
            continue
        if function_has_obvious_side_effects(func):
            continue
        required_args = [
            arg for arg in func.args.args
            if arg.arg != "self"
        ]
        if len(required_args) > 3 or func.args.vararg or func.args.kwarg or func.args.kwonlyargs:
            continue
        samples = []
        for arg in required_args:
            sample = infer_sample_value(arg.arg, arg.annotation)
            if sample is None:
                samples = []
                break
            samples.append(sample)
        if len(required_args) == 0:
            samples = []
        if required_args and not samples:
            continue
        lowered_name = func.name.lower()
        confidence = 0.75 if lowered_name in PURE_FUNCTION_NAME_HINTS or any(hint in lowered_name for hint in PURE_FUNCTION_NAME_HINTS) else 0.68
        command = f"function:{func.name}"
        result["considered"] = True
        result["functions"] = [func.name]
        result["used"] = confidence >= 0.75
        result["confidence"] = "high" if confidence >= 0.8 else "medium"
        result["reason"] = f"Detected a likely pure helper `{func.name}` with safely inferred sample arguments."
        result["step"] = {
            "command": command,
            "kind": "function",
            "confidence": confidence,
            "reason": result["reason"],
            "source": script_rel,
            "function_name": func.name,
            "sample_args": samples,
        }
        return result
    return result


def build_script_validation_plan(repo: Path, script_path: Path) -> dict:
    repo = repo.resolve()
    script_path = script_path.resolve()
    script_rel = relative_script_path(repo, script_path)
    module_name = infer_module_name(repo, script_path)
    features = extract_script_features(script_path)
    candidates: list[dict] = []

    syntax_candidate = {
        "command": f"python -m py_compile {shlex.quote(script_rel)}",
        "kind": "syntax",
        "confidence": 1.0,
        "reason": "Syntax validation is always included for script mode.",
        "source": script_rel,
    }
    candidates.append(syntax_candidate)

    pytest_candidates = find_nearby_pytest_targets(repo, script_path)
    candidates.extend(pytest_candidates)

    executable = bool(features.get("has_main_guard") or features.get("entrypoints"))
    cli_like = bool(features.get("uses_argparse") or features.get("uses_click") or features.get("uses_typer"))
    if executable:
        candidates.append(
            {
                "command": f"python {shlex.quote(script_rel)} --help",
                "kind": "cli_help",
                "confidence": 0.85 if cli_like else 0.55,
                "reason": "The script looks executable and `--help` is usually safe.",
                "source": script_rel,
            }
        )
        candidates.append(
            {
                "command": f"python {shlex.quote(script_rel)}",
                "kind": "cli_run",
                "confidence": 0.4 if cli_like else 0.3,
                "reason": "The script has an executable entrypoint, but running it without arguments may be risky.",
                "source": script_rel,
            }
        )
    if module_name:
        candidates.append(
            {
                "command": f"python -m {module_name} --help",
                "kind": "module_help",
                "confidence": 0.7 if cli_like else 0.45,
                "reason": "The script can be addressed as a Python module.",
                "source": module_name,
            }
        )
        candidates.append(
            {
                "command": f"python -m {module_name}",
                "kind": "module_run",
                "confidence": 0.35,
                "reason": "Module execution is available but may require arguments.",
                "source": module_name,
            }
        )

    import_candidate = {
        "command": (
            "python -c "
            "\"import importlib.util, pathlib, sys; "
            "p = pathlib.Path(sys.argv[1]); "
            "spec = importlib.util.spec_from_file_location('_lfa_script', p); "
            "mod = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod)\" "
            f"{shlex.quote(script_rel)}"
        ),
        "kind": "import",
        "confidence": 0.5,
        "reason": "Fallback import/module-load validation when no stronger runtime command is available.",
        "source": script_rel,
    }
    candidates.append(import_candidate)
    candidates.extend(discover_context_candidates(repo, script_rel, module_name, script_path.stem))

    function_validation = discover_function_validation(script_path, script_rel, features)
    if function_validation.get("step") and function_validation.get("used"):
        candidates.append(function_validation["step"])

    ranked = sorted(candidates, key=lambda item: item.get("confidence", 0), reverse=True)
    non_syntax = [item for item in ranked if item.get("kind") != "syntax"]
    primary_candidates = [item for item in non_syntax if item.get("kind") != "function"]
    chosen_extra = primary_candidates[0] if primary_candidates else None
    if chosen_extra and chosen_extra.get("kind") == "cli_run":
        safer_cli = next((item for item in primary_candidates if item.get("kind") in {"cli_help", "module_help"}), None)
        if safer_cli and safer_cli.get("confidence", 0) >= 0.45:
            chosen_extra = safer_cli
    limited_validation = not chosen_extra or chosen_extra.get("kind") == "import"
    chosen_stack = [syntax_candidate]
    if chosen_extra and chosen_extra["command"] != syntax_candidate["command"]:
        chosen_stack.append(chosen_extra)
    if function_validation.get("step") and function_validation.get("used"):
        chosen_stack.append(function_validation["step"])

    primary_command = chosen_extra["command"] if chosen_extra else syntax_candidate["command"]
    confidence_value = chosen_extra.get("confidence", 0.5) if chosen_extra else 0.5
    confidence_level = "high" if confidence_value >= 0.85 else ("medium" if confidence_value >= 0.6 else "low")
    only_syntax_import = all(step.get("kind") in {"syntax", "import"} for step in chosen_stack)
    probe_recommendations = recommend_script_probe_targets(features)
    network_dependency = classify_network_dependency(features)
    suggested_probe_headers = extract_probe_header_candidates(features.get("text") or "")
    return {
        "active": True,
        "mode": "script",
        "repo": str(repo),
        "script_path": str(script_path),
        "script_rel_path": script_rel,
        "module_name": module_name,
        "candidates": ranked,
        "chosen_stack": chosen_stack,
        "primary_command": primary_command,
        "confidence_level": confidence_level,
        "function_validation": function_validation,
        "network_dependency": network_dependency,
        "probe_recommendations": probe_recommendations,
        "suggested_probe_headers": suggested_probe_headers,
        "auto_probe_evaluated": False,
        "auto_probe_used": False,
        "probe_findings": [],
        "probe_reasoning": "",
        "limited_validation": limited_validation,
        "only_syntax_import_validation": only_syntax_import,
        "limited_reason": (
            "Validation is limited to syntax/import checks; no strong runtime or test command was found."
            if limited_validation
            else ""
        ),
    }


def run_import_validation(repo: Path, step: dict) -> tuple[int, str]:
    script_rel = step.get("source") or CURRENT_VALIDATION_PLAN.get("script_rel_path", "")
    import_code = (
        "import importlib.util, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('_lfa_script', path)\n"
        "if spec is None or spec.loader is None:\n"
        "    raise RuntimeError(f'Could not load module from {path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(f'import-ok:{path.name}')\n"
    )
    return run_subprocess([sys.executable, "-c", import_code, script_rel], repo)


def run_function_validation(repo: Path, step: dict) -> tuple[int, str]:
    script_rel = step.get("source") or CURRENT_VALIDATION_PLAN.get("script_rel_path", "")
    function_name = step.get("function_name", "")
    sample_args = json.dumps(step.get("sample_args", []))
    harness = (
        "import importlib.util, json, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "func_name = sys.argv[2]\n"
        "args = json.loads(sys.argv[3])\n"
        "spec = importlib.util.spec_from_file_location('_lfa_script', path)\n"
        "if spec is None or spec.loader is None:\n"
        "    raise RuntimeError(f'Could not load module from {path}')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "fn = getattr(module, func_name)\n"
        "first = fn(*args)\n"
        "second = fn(*args)\n"
        "assert first == second, 'function output changed across identical calls'\n"
        "print(json.dumps({'function': func_name, 'result': repr(first)[:120]}))\n"
    )
    return run_subprocess([sys.executable, "-c", harness, script_rel, function_name, sample_args], repo)


def run_validation_stack(repo: Path, validation_plan: dict, include_syntax: bool = True) -> dict:
    steps = validation_plan.get("chosen_stack", [])
    results: list[dict] = []
    for step in steps:
        kind = step.get("kind", "")
        if kind == "syntax" and not include_syntax:
            continue
        if kind == "import":
            code, output = run_import_validation(repo, step)
        elif kind == "function":
            code, output = run_function_validation(repo, step)
        else:
            code, output = run_subprocess(step.get("command", ""), repo, shell=True)
        result = {
            "ok": code == 0,
            "kind": kind,
            "command": step.get("command", ""),
            "output": output.strip(),
            "failure_type": classify_failure_type(output),
        }
        results.append(result)
        if code != 0:
            return {
                "ok": False,
                "results": results,
                "failed_step": result,
                "output": output.strip(),
                "failure_type": result["failure_type"],
            }
    return {"ok": True, "results": results, "failed_step": {}, "output": "", "failure_type": FAILURE_UNKNOWN}


def format_validation_plan_summary(plan: dict) -> str:
    lines = [
        "=== SCRIPT VALIDATION DISCOVERY ===",
        "Discovered validation candidates:",
    ]
    for candidate in plan.get("candidates", [])[:8]:
        lines.append(
            f"- [{candidate.get('kind')}] {candidate.get('command')} "
            f"(confidence={candidate.get('confidence', 0):.2f}; source={candidate.get('source', '')})"
        )
    lines.append("Chosen validation stack:")
    for step in plan.get("chosen_stack", []):
        lines.append(f"- [{step.get('kind')}] {step.get('command')}")
    func_info = plan.get("function_validation", {})
    lines.append(
        "Function-level validation: "
        + (
            f"used ({', '.join(func_info.get('functions', []))})"
            if func_info.get("used")
            else ("considered but skipped" if func_info.get("considered") else "not used")
        )
    )
    lines.append(f"Chosen validation confidence: {plan.get('confidence_level', 'low')}")
    if plan.get("only_syntax_import_validation"):
        lines.append("Validation coverage: limited to syntax/import checks.")
    if plan.get("limited_reason"):
        lines.append(plan["limited_reason"])
    dependency = plan.get("network_dependency") or {}
    if dependency.get("detected"):
        lines.append(
            "Network dependency: "
            f"{dependency.get('confidence', 'low')} "
            f"({dependency.get('reason') or 'network-like behavior detected'})"
        )
    if plan.get("probe_recommendations"):
        lines.append("Suggested live probes:")
        for item in plan.get("probe_recommendations", [])[:3]:
            endpoint = item.get("endpoint") or "<endpoint required>"
            lines.append(f"- [{item.get('probe_type')}] {endpoint} ({item.get('reason') or 'live endpoint evidence may help'})")
    if plan.get("auto_probe_used"):
        lines.append("Automatic probe findings:")
        for item in plan.get("probe_findings", [])[:2]:
            lines.append(
                f"- [{item.get('probe_type')}] {item.get('endpoint') or '(none)'} "
                f"status={item.get('status_code') or 0} summary={item.get('summary') or item.get('error') or '(none)'}"
            )
    if plan.get("probe_reasoning"):
        lines.append(f"Probe reasoning: {plan.get('probe_reasoning')}")
    return "\n".join(lines)


def print_script_validation_only_result(script_path: Path, plan: dict, validation_run: dict, probe_planned: bool = False) -> None:
    print("\n=== SCRIPT VALIDATION RESULT ===")
    print(f"script_path: {script_path}")
    print(f"validation_command: {plan.get('primary_command') or '(auto-detect)'}")
    print(f"validation_result: {'success' if validation_run.get('ok') else 'blocked'}")
    if validation_run.get("failed_step"):
        print(f"failed_step: {(validation_run.get('failed_step') or {}).get('command') or '(none)'}")
    if plan.get("probe_reasoning"):
        print(f"probe_reasoning: {plan.get('probe_reasoning')}")
    print(f"probing_used: {format_bool(probe_planned or plan.get('auto_probe_used'))}")
    if not validation_run.get("ok"):
        print(f"blocked_reason: {validation_run.get('output') or 'validation failed'}")


def pattern_keywords_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower()):
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


def relative_or_name(path: Path, repo: Path) -> str:
    resolved = path.resolve()
    repo_resolved = repo.resolve()
    if resolved.is_relative_to(repo_resolved):
        return str(resolved.relative_to(repo_resolved))
    return path.name


def detect_cli_style(features: dict) -> str:
    if features.get("uses_typer"):
        return "typer"
    if features.get("uses_click"):
        return "click"
    if features.get("uses_argparse"):
        return "argparse"
    return "manual"


def infer_script_pattern_task_tags(pattern_type: str, text: str) -> list[str]:
    tags = {
        "debug",
        "refactor",
    }
    if pattern_type in {"cli_style", "entrypoint", "logging_style", "config_loading", "validation_strategy"}:
        tags.update({"new-script", "validation_discovery"})
    if pattern_type in {"proxy_handling", "request_session", "retry_backoff", "timeout", "rate_limit_handling"}:
        tags.update({"new-script", "validation_discovery"})
    if pattern_type == "function_organization":
        tags.update({"new-script", "validation_discovery"})
    if "local" in text.lower() or "filesystem" in text.lower():
        tags.add("new-script")
    return sorted(tags)


def make_script_pattern_entry(
    repo: Path,
    script_path: Path,
    pattern_type: str,
    summary: str,
    confidence: float,
    keywords: list[str],
    source_record: dict | None = None,
    normalized_examples: dict | None = None,
    anti_pattern_note: str = "",
) -> dict:
    source_rel = relative_or_name(script_path, repo)
    suffix = ""
    if normalized_examples and normalized_examples.get("style"):
        suffix = f"-{normalized_examples['style']}"
    pattern_id = re.sub(r"[^a-z0-9_.-]+", "-", f"{source_rel}-{pattern_type}{suffix}".lower()).strip("-")
    return {
        "id": pattern_id,
        "name": pattern_type.replace("_", " "),
        "family": pattern_type,
        "source_files": [source_rel],
        "source_repo_path": str((source_record or {}).get("repo_rel_path") or source_rel),
        "source_origin": str((source_record or {}).get("origin_path") or ""),
        "source_subpath": str((source_record or {}).get("source_subpath") or ""),
        "import_scope": str((source_record or {}).get("import_scope") or "file"),
        "trust_level": str((source_record or {}).get("trust_level") or "trusted"),
        "tags": list((source_record or {}).get("tags") or []),
        "pattern_type": pattern_type,
        "summary": summary,
        "applicability_context": infer_script_pattern_task_tags(pattern_type, summary),
        "confidence": round(confidence, 2),
        "keywords": sorted(set(keywords))[:16],
        "normalized_examples": normalized_examples or {},
        "anti_pattern_note": anti_pattern_note,
        "success_count": 0,
    }


def extract_script_patterns(repo: Path, script_path: Path) -> list[dict]:
    return extract_script_patterns_with_metadata(repo, script_path, None)


def extract_script_patterns_with_metadata(repo: Path, script_path: Path, source_record: dict | None) -> list[dict]:
    features = extract_script_features(script_path)
    if not features.get("text"):
        return []
    text = features.get("text", "")
    lowered = text.lower()
    source_rel = relative_or_name(script_path, repo)
    plan = build_script_validation_plan(repo, script_path)
    patterns: list[dict] = []
    base_keywords = pattern_keywords_from_text(f"{source_rel} {script_path.stem} {lowered[:4000]}")

    def add(
        pattern_type: str,
        summary: str,
        confidence: float,
        extra_keywords: list[str],
        normalized_examples: dict | None = None,
        anti_pattern_note: str = "",
    ) -> None:
        patterns.append(
            make_script_pattern_entry(
                repo,
                script_path,
                pattern_type,
                summary,
                confidence,
                base_keywords + extra_keywords,
                source_record=source_record,
                normalized_examples=normalized_examples,
                anti_pattern_note=anti_pattern_note,
            )
        )

    cli_style = detect_cli_style(features)
    if cli_style != "manual":
        add(
            "cli_style",
            f"Uses {cli_style} for command-line parsing and operator-facing help output.",
            0.95,
            ["cli", "help", cli_style],
            normalized_examples={"style": cli_style, "validation_kind": "cli_help"},
        )
    if features.get("has_main_guard") or features.get("entrypoints"):
        entrypoint_name = (features.get("entrypoints") or ["main"])[0]
        add(
            "entrypoint",
            f"Uses a {entrypoint_name}() entrypoint behind a __main__ guard.",
            0.9,
            ["entrypoint", entrypoint_name, "main"],
            normalized_examples={"style": entrypoint_name},
        )
    if "logging" in lowered:
        add(
            "logging_style",
            "Uses structured logging calls instead of print-only status output.",
            0.8,
            ["logging", "log", "logger"],
            normalized_examples={"style": "logging"},
        )
    if any(token in lowered for token in ["os.getenv", "os.environ", "json.load", "tomllib", "configparser", "yaml.safe_load"]):
        add(
            "config_loading",
            "Loads configuration from environment variables or config files.",
            0.75,
            ["config", "env", "settings"],
            normalized_examples={"style": "env_or_file"},
        )
    if any(token in lowered for token in ["proxy", "http_proxy", "https_proxy", "proxyhandler"]):
        add(
            "proxy_handling",
            "Handles outbound proxy configuration explicitly.",
            0.9,
            ["proxy", "network", "http_proxy", "https_proxy"],
            normalized_examples={"style": "proxy_aware"},
        )
    if any(token in lowered for token in ["request.", "urlopen(", "build_opener(", "session(", "proxyhandler("]):
        add(
            "request_session",
            "Wraps outbound requests in a dedicated request/opening helper.",
            0.72,
            ["network", "request", "http", "url"],
            normalized_examples={"style": "request_helper"},
        )
    if any(token in lowered for token in ["timeout=", "socket.setdefaulttimeout", "timeout: float", "--timeout"]):
        add(
            "timeout",
            "Sets explicit timeout values for external work.",
            0.82,
            ["timeout", "latency", "network"],
            normalized_examples={"style": "explicit_timeout"},
        )
    if any(token in lowered for token in ["retry", "backoff", "attempt", "time.sleep("]):
        add(
            "retry_backoff",
            "Uses retry/backoff loops around flaky external operations.",
            0.88,
            ["retry", "backoff", "attempt", "sleep"],
            normalized_examples={"style": "loop_retry"},
        )
    if any(token in lowered for token in ["429", "retry-after", "rate limit", "too many requests", "ratelimit"]):
        add(
            "rate_limit_handling",
            "Recognizes rate-limit responses and handles them explicitly.",
            0.86,
            ["429", "retry-after", "rate", "limit"],
            normalized_examples={"style": "rate_limit"},
        )
    if "try:" in lowered and "except" in lowered:
        anti_pattern = "Avoid broad bare except blocks." if re.search(r"except\s*:\s*", text) else ""
        add(
            "error_handling",
            "Uses explicit exception handling around risky operations.",
            0.7,
            ["error", "except", "failure"],
            normalized_examples={"style": "try_except"},
            anti_pattern_note=anti_pattern,
        )
    if features.get("functions"):
        helper_names = [
            func.name for func in features.get("functions", [])
            if func.name not in {"main", "run", "cli"} and not func.name.startswith("__")
        ]
        if helper_names:
            add(
                "function_organization",
                "Breaks implementation into named helper functions instead of a single monolith.",
                0.68,
                helper_names[:4] + ["helpers", "functions"],
                normalized_examples={"style": "helper_functions"},
            )

    validation_kind = ""
    validation_source = ""
    if plan.get("function_validation", {}).get("used"):
        validation_kind = "function"
        validation_source = plan.get("script_rel_path", "")
    else:
        for step in plan.get("chosen_stack", []):
            kind = step.get("kind", "")
            if kind != "syntax":
                validation_kind = kind
                validation_source = step.get("source", "")
                break
    if validation_kind:
        add(
            "validation_strategy",
            f"Prefers {validation_kind} validation for this script shape.",
            0.83,
            ["validation", validation_kind, validation_source or script_path.stem],
            normalized_examples={"style": validation_kind, "validation_kind": validation_kind},
        )
    return patterns


def import_pattern_tags(pattern_tags: str | None) -> list[str]:
    if not pattern_tags:
        return []
    tags = []
    for item in str(pattern_tags).split(","):
        candidate = item.strip()
        if candidate and candidate not in tags:
            tags.append(candidate)
    return tags


def slugify_pattern_import_name(path: Path | str) -> str:
    candidate_path = path if isinstance(path, Path) else Path(str(path))
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate_path.stem).strip("-").lower() or "script"
    return stem[:60]


def parse_pattern_import_source(source: str) -> dict:
    text = str(source or "").strip()
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        filename = Path(parsed.path or "script.py").name or "script.py"
        return {
            "source_type": "http",
            "origin": text,
            "display_name": filename,
            "ssh_host": "",
            "ssh_path": "",
            "scheme": parsed.scheme,
        }
    if parsed.scheme == "ssh":
        ssh_host = parsed.netloc
        ssh_path = parsed.path or ""
        filename = Path(ssh_path or "script.py").name or "script.py"
        return {
            "source_type": "ssh",
            "origin": text,
            "display_name": filename,
            "ssh_host": ssh_host,
            "ssh_path": ssh_path,
            "scheme": "ssh",
        }
    legacy_match = re.match(r"^(?P<host>[^:@/\s]+@[^:/\s]+):(?P<path>/.*)$", text)
    if legacy_match:
        ssh_path = legacy_match.group("path")
        filename = Path(ssh_path or "script.py").name or "script.py"
        return {
            "source_type": "ssh",
            "origin": text,
            "display_name": filename,
            "ssh_host": legacy_match.group("host"),
            "ssh_path": ssh_path,
            "scheme": "ssh",
        }
    path = Path(text).expanduser()
    return {
        "source_type": "local",
        "origin": str(path.resolve() if path.exists() else path),
        "display_name": path.name or "script.py",
        "ssh_host": "",
        "ssh_path": "",
        "scheme": "",
    }


def pattern_source_proxy_used(source_type: str) -> bool:
    if source_type != "http":
        return False
    return any(
        bool(CURRENT_SUBPROCESS_ENV.get(key))
        for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]
    )


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def fetch_pattern_source(source: str, cwd: Path) -> dict:
    parsed = parse_pattern_import_source(source)
    source_type = parsed["source_type"]
    origin = parsed["origin"]
    if source_type == "local":
        path = Path(origin)
        if not path.exists() or not path.is_file():
            return {
                "ok": False,
                "source_type": "local",
                "source_origin": origin,
                "acquisition_method": "direct",
                "proxy_used": False,
                "blocked_reason": f"local source not found: {origin}",
                "content": "",
                "display_name": parsed["display_name"],
            }
        try:
            content = path.read_text()
        except OSError as exc:
            return {
                "ok": False,
                "source_type": "local",
                "source_origin": origin,
                "acquisition_method": "direct",
                "proxy_used": False,
                "blocked_reason": f"local source could not be read: {exc}",
                "content": "",
                "display_name": parsed["display_name"],
            }
        return {
            "ok": True,
            "source_type": "local",
            "source_origin": origin,
            "acquisition_method": "direct",
            "proxy_used": False,
            "content": content,
            "display_name": parsed["display_name"],
        }
    if source_type == "http":
        if not command_available("curl"):
            return {
                "ok": False,
                "source_type": "http",
                "source_origin": origin,
                "acquisition_method": "curl",
                "proxy_used": pattern_source_proxy_used("http"),
                "blocked_reason": "missing required tool: curl",
                "content": "",
                "display_name": parsed["display_name"],
            }
        code, output = run_subprocess(["curl", "-fsSL", origin], cwd)
        if code != 0:
            return {
                "ok": False,
                "source_type": "http",
                "source_origin": origin,
                "acquisition_method": "curl",
                "proxy_used": pattern_source_proxy_used("http"),
                "blocked_reason": f"http fetch failed: {output.strip() or 'curl returned non-zero'}",
                "content": "",
                "display_name": parsed["display_name"],
            }
        return {
            "ok": True,
            "source_type": "http",
            "source_origin": origin,
            "acquisition_method": "curl",
            "proxy_used": pattern_source_proxy_used("http"),
            "content": output,
            "display_name": parsed["display_name"],
        }
    if source_type == "ssh":
        remote_spec = origin if "@" in origin and ":" in origin and not origin.startswith("ssh://") else f"{parsed['ssh_host']}:{parsed['ssh_path']}"
        if command_available("scp"):
            with tempfile.TemporaryDirectory(prefix="lfa-pattern-fetch-") as tmpdir:
                destination = Path(tmpdir) / parsed["display_name"]
                code, output = run_subprocess(["scp", "-q", remote_spec, str(destination)], cwd)
                if code == 0 and destination.exists():
                    try:
                        content = destination.read_text()
                    except OSError as exc:
                        return {
                            "ok": False,
                            "source_type": "ssh",
                            "source_origin": origin,
                            "acquisition_method": "scp",
                            "proxy_used": False,
                            "blocked_reason": f"ssh fetch read failed: {exc}",
                            "content": "",
                            "display_name": parsed["display_name"],
                        }
                    return {
                        "ok": True,
                        "source_type": "ssh",
                        "source_origin": origin,
                        "acquisition_method": "scp",
                        "proxy_used": False,
                        "content": content,
                        "display_name": parsed["display_name"],
                    }
        if not command_available("ssh"):
            return {
                "ok": False,
                "source_type": "ssh",
                "source_origin": origin,
                "acquisition_method": "ssh",
                "proxy_used": False,
                "blocked_reason": "missing required tool: ssh (and scp unavailable)",
                "content": "",
                "display_name": parsed["display_name"],
            }
        code, output = run_subprocess(["ssh", parsed["ssh_host"], "cat", parsed["ssh_path"]], cwd)
        if code != 0:
            return {
                "ok": False,
                "source_type": "ssh",
                "source_origin": origin,
                "acquisition_method": "ssh",
                "proxy_used": False,
                "blocked_reason": f"ssh fetch failed: {output.strip() or 'ssh returned non-zero'}",
                "content": "",
                "display_name": parsed["display_name"],
            }
        return {
            "ok": True,
            "source_type": "ssh",
            "source_origin": origin,
            "acquisition_method": "ssh",
            "proxy_used": False,
            "content": output,
            "display_name": parsed["display_name"],
        }
    return {
        "ok": False,
        "source_type": source_type,
        "source_origin": origin,
        "acquisition_method": "direct",
        "proxy_used": False,
        "blocked_reason": "unsupported pattern import source",
        "content": "",
        "display_name": parsed["display_name"],
    }


def pattern_repo_source_id(source: Path | str, trust_level: str) -> str:
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
    return f"{trust_level}-{slugify_pattern_import_name(source)}-{digest}"


def infer_pattern_import_destination(pattern_repo: Path, source_path: Path | str, trust_level: str) -> Path:
    source_name = str(source_path)
    suffix = Path(source_name).suffix or ".py"
    slug = slugify_pattern_import_name(source_name)
    digest = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:10]
    return ensure_pattern_repo(pattern_repo) / "candidates" / f"{slug}-{digest}{suffix}"


def infer_curated_pattern_destination(pattern_repo: Path, source_path: Path | str, trust_level: str) -> Path:
    source_name = str(source_path)
    suffix = Path(source_name).suffix or ".py"
    slug = slugify_pattern_import_name(source_name)
    digest = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:10]
    return ensure_pattern_repo(pattern_repo) / "curated" / trust_level / f"{slug}-{digest}{suffix}"


def pattern_collection_storage_name(source_root: Path | str) -> str:
    source_name = str(source_root)
    slug = slugify_pattern_import_name(Path(source_name).name or source_name)
    digest = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def infer_pattern_collection_candidate_destination(pattern_repo: Path, collection_name: str, source_subpath: str) -> Path:
    repo_root = ensure_pattern_repo(pattern_repo)
    rel = PurePosixPath(str(source_subpath or "").strip("/"))
    return repo_root / "imports" / "candidates" / collection_name / rel


def infer_pattern_collection_curated_destination(pattern_repo: Path, collection_name: str, source_subpath: str, trust_level: str) -> Path:
    repo_root = ensure_pattern_repo(pattern_repo)
    rel = PurePosixPath(str(source_subpath or "").strip("/"))
    return repo_root / "imports" / trust_level / collection_name / rel


def normalize_collection_globs(values: Sequence[str] | None, default: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for item in values or []:
        candidate = str(item or "").strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized or list(default)


def classify_pattern_import_scope(source_root: Path) -> str:
    root = source_root.resolve()
    return "repo" if (root / ".git").exists() else "folder"


def summarize_path_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix or "<no_ext>"


def classify_validation_command_kind(command: str) -> str:
    lowered = str(command or "").lower()
    if not lowered:
        return ""
    if "pytest" in lowered:
        return "pytest"
    if "py_compile" in lowered:
        return "syntax"
    if "--help" in lowered:
        return "cli_help"
    if "python -c" in lowered or "python3 -c" in lowered:
        return "function"
    return "custom"


def naming_style_for_path(path: Path) -> str:
    stem = path.stem
    if re.fullmatch(r"[a-z0-9_]+", stem):
        return "snake_case"
    if re.fullmatch(r"[a-z0-9-]+", stem):
        return "kebab_case"
    return "mixed"


def scan_pattern_source_collection(
    source_root: Path,
    *,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
    max_files: int = DEFAULT_PATTERN_REPO_MAX_FILES,
    max_depth: int = 0,
) -> dict:
    root = source_root.expanduser().resolve()
    include_patterns = normalize_collection_globs(include_globs, DEFAULT_PATTERN_REPO_INCLUDE_GLOBS)
    exclude_patterns = normalize_collection_globs(exclude_globs, [])
    if not root.exists() or not root.is_dir():
        return {
            "ok": False,
            "source_root": str(root),
            "import_scope": "folder",
            "candidate_paths": [],
            "ignored_paths": [],
            "ignored_count": 0,
            "file_type_counts": {},
            "blocked_reason": f"source collection not found: {root}",
            "include_globs": include_patterns,
            "exclude_globs": exclude_patterns,
        }
    candidates: list[Path] = []
    ignored_paths: list[str] = []
    file_type_counts: dict[str, int] = {}
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_dir = current_path.relative_to(root)
        dirnames[:] = [name for name in dirnames if name not in IGNORE_DIRS and not name.startswith(".")]
        if max_depth and rel_dir != Path(".") and len(rel_dir.parts) >= max_depth:
            dirnames[:] = []
        for filename in sorted(filenames):
            if filename.startswith("."):
                ignored_paths.append(str((rel_dir / filename).as_posix()))
                continue
            rel_path = (rel_dir / filename) if rel_dir != Path(".") else Path(filename)
            rel_text = rel_path.as_posix()
            file_type_counts[summarize_path_suffix(rel_path)] = file_type_counts.get(summarize_path_suffix(rel_path), 0) + 1
            if max_depth and len(rel_path.parts) - 1 > max_depth:
                ignored_paths.append(rel_text)
                continue
            pure_rel = PurePosixPath(rel_text)
            if exclude_patterns and any(pure_rel.match(pattern) for pattern in exclude_patterns):
                ignored_paths.append(rel_text)
                continue
            if include_patterns and not any(pure_rel.match(pattern) for pattern in include_patterns):
                ignored_paths.append(rel_text)
                continue
            candidates.append(root / rel_path)
            if max_files and len(candidates) >= max_files:
                break
        if max_files and len(candidates) >= max_files:
            break
    return {
        "ok": True,
        "source_root": str(root),
        "import_scope": classify_pattern_import_scope(root),
        "candidate_paths": [str(path) for path in candidates],
        "candidate_rel_paths": [str(path.relative_to(root).as_posix()) for path in candidates],
        "candidate_count": len(candidates),
        "ignored_paths": ignored_paths[:20],
        "ignored_count": len(ignored_paths),
        "file_type_counts": dict(sorted(file_type_counts.items())),
        "include_globs": include_patterns,
        "exclude_globs": exclude_patterns,
        "collection_name": pattern_collection_storage_name(root),
        "blocked_reason": "",
    }


def sanitize_pattern_script_content(content: str) -> tuple[str, bool]:
    sanitized = content
    replacements = [
        (
            re.compile(r"(?i)\b(authorization\s*[:=]\s*[\"']?bearer\s+)[A-Za-z0-9._~+/=-]+([\"']?)"),
            r"\1<REDACTED_BEARER_TOKEN>\2",
        ),
        (
            re.compile(r"(?i)\b(cookie\s*[:=]\s*[\"']?)[^\"'\n]+([\"']?)"),
            r"\1<REDACTED_COOKIE>\2",
        ),
        (
            re.compile(r"(?i)\b(api[_-]?key|token|password|passwd|secret)\b(\s*[:=]\s*[\"']?)([^\"'\n]+)([\"']?)"),
            r"\1\2<REDACTED_SECRET>\4",
        ),
        (
            re.compile(r"(?i)\b(http_proxy|https_proxy)\b(\s*[:=]\s*[\"']?)(https?://)([^:@/\s]+):([^@/\s]+)@"),
            r"\1\2\3<REDACTED_USER>:<REDACTED_PASS>@",
        ),
        (
            re.compile(r"(?i)\b(sk-[A-Za-z0-9]{12,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"),
            "<REDACTED_TOKEN>",
        ),
        (
            re.compile(r"(?i)\b(authorization|cookie|set-cookie)\b"),
            lambda match: match.group(0),
        ),
        (
            re.compile(r"(?i)\b([A-Za-z_][A-Za-z0-9_]*_?(?:key|token|password|secret))\b(\s*=\s*os\.getenv\(\s*[\"'])([^\"']+)([\"']\s*\))"),
            r"\1\2<REDACTED_ENV_NAME>\4",
        ),
        (
            re.compile(r"(?i)\b(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+)(:\d+)?\b"),
            "<REDACTED_HOST>",
        ),
    ]
    for pattern, replacement in replacements:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized, sanitized != content


def import_pattern_source_record(
    pattern_repo: Path,
    source_value: str,
    *,
    trust_level: str,
    tags: list[str] | None = None,
    note: str = "",
    candidate_path: Path | None = None,
    curated_path: Path | None = None,
    source_metadata: dict | None = None,
) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    trust = trust_level if trust_level in PATTERN_TRUST_LEVELS else "trusted"
    metadata = dict(source_metadata or {})
    fetched = fetch_pattern_source(source_value, repo_root)
    source_origin = str(fetched.get("source_origin") or source_value)
    candidate_destination = candidate_path or infer_pattern_import_destination(repo_root, source_origin, trust)
    candidate_destination.parent.mkdir(parents=True, exist_ok=True)
    source_id = str(metadata.get("id") or pattern_repo_source_id(candidate_destination.relative_to(repo_root), trust))
    base_record = {
        "id": source_id,
        "repo_rel_path": "",
        "candidate_path": "",
        "origin_path": source_origin,
        "source_type": fetched.get("source_type", "local"),
        "source_origin": source_origin,
        "acquisition_method": fetched.get("acquisition_method", "direct"),
        "proxy_used": bool(fetched.get("proxy_used")),
        "sanitized_path": "",
        "trust_level": trust,
        "tags": list(tags or []),
        "note": note,
        "imported_at": int(time.time()),
        "sanitized_changed": False,
        "sanitization_applied": False,
        "validation_status": "blocked",
        "validation_passed": False,
        "validation_command": "",
        "repair_needed": False,
        "repair_output": "",
        "promotion_state": "candidate",
        "promotion_state_detail": "candidate",
        "promoted": False,
        "candidate_imported": False,
        "limited_validation": False,
        "blocked_reason": "",
        "final_destination": "",
        "import_scope": str(metadata.get("import_scope") or "file"),
        "source_repo_path": str(metadata.get("source_repo_path") or ""),
        "source_subpath": str(metadata.get("source_subpath") or Path(source_origin).name),
        "source_kind": str(metadata.get("source_kind") or "file"),
        "collection_name": str(metadata.get("collection_name") or ""),
        "detected_pattern_families": [],
    }
    if not fetched.get("ok"):
        base_record["blocked_reason"] = str(fetched.get("blocked_reason") or "source acquisition failed")
        return base_record
    raw_content = str(fetched.get("content") or "")
    sanitized_content, changed = sanitize_pattern_script_content(raw_content)
    candidate_destination.write_text(sanitized_content)
    validation = run_candidate_validation(repo_root, candidate_destination)
    repaired = False
    repair_result = {"ok": False, "output": "", "command": []}
    if not validation.get("passed"):
        repair_result = repair_training_candidate(repo_root, candidate_destination)
        if repair_result.get("ok"):
            repaired = True
            validation = run_candidate_validation(repo_root, candidate_destination)
    blocked = not validation.get("passed")
    promote = bool(validation.get("passed")) and not blocked and not validation.get("limited_validation")
    final_destination = curated_path or infer_curated_pattern_destination(repo_root, source_origin, trust)
    repo_rel_path = str(candidate_destination.relative_to(repo_root))
    curated_rel = ""
    if promote:
        final_destination.parent.mkdir(parents=True, exist_ok=True)
        final_destination.write_text(candidate_destination.read_text())
        curated_rel = str(final_destination.relative_to(repo_root))
        repo_rel_path = curated_rel
    record = {
        **base_record,
        "repo_rel_path": repo_rel_path,
        "candidate_path": str(candidate_destination.relative_to(repo_root)),
        "sanitized_path": str(candidate_destination),
        "sanitized_changed": changed,
        "sanitization_applied": True,
        "validation_status": "passed" if validation.get("passed") else ("blocked" if blocked else "failed"),
        "validation_passed": bool(validation.get("passed")),
        "validation_command": validation.get("validation_command", ""),
        "repair_needed": repaired,
        "repair_output": repair_result.get("output", ""),
        "promotion_state": "curated" if promote else "candidate",
        "promotion_state_detail": (f"curated_{trust}" if promote else "candidate"),
        "promoted": promote,
        "candidate_imported": True,
        "limited_validation": bool(validation.get("limited_validation")),
        "blocked_reason": "" if validation.get("passed") else (repair_result.get("output", "") or "validation blocked"),
        "final_destination": curated_rel,
    }
    analysis_path = repo_root / repo_rel_path
    if analysis_path.exists() and analysis_path.is_file():
        extracted = extract_script_patterns_with_metadata(repo_root, analysis_path, record)
        record["detected_pattern_families"] = sorted(
            {
                str(item.get("family") or item.get("pattern_type") or "").strip()
                for item in extracted
                if str(item.get("family") or item.get("pattern_type") or "").strip()
            }
        )
    return record


def build_pattern_collection_summary(
    pattern_repo: Path,
    source_root: Path,
    scan_summary: dict,
    imported_sources: list[dict],
    *,
    trust_level: str,
    tags: list[str] | None = None,
) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    promoted_sources = [item for item in imported_sources if item.get("promoted")]
    family_counts: dict[str, int] = {}
    helper_counts: dict[str, int] = {}
    top_directories: dict[str, int] = {}
    validation_kinds: dict[str, int] = {}
    naming_counts: dict[str, int] = {}
    for source in promoted_sources:
        for family in source.get("detected_pattern_families") or []:
            family_counts[str(family)] = family_counts.get(str(family), 0) + 1
        subpath = Path(str(source.get("source_subpath") or ""))
        if subpath.parts:
            top_directories[subpath.parts[0]] = top_directories.get(subpath.parts[0], 0) + 1
        naming = naming_style_for_path(subpath if subpath.name else Path(str(source.get("origin_path") or "script.py")))
        naming_counts[naming] = naming_counts.get(naming, 0) + 1
        validation_kind = classify_validation_command_kind(str(source.get("validation_command") or ""))
        if validation_kind:
            validation_kinds[validation_kind] = validation_kinds.get(validation_kind, 0) + 1
        stored_rel = str(source.get("repo_rel_path") or source.get("candidate_path") or "")
        stored_path = repo_root / stored_rel if stored_rel else None
        if stored_path and stored_path.exists() and stored_path.is_file():
            features = extract_script_features(stored_path)
            for func in features.get("functions", []):
                if func.name not in {"main", "run", "cli"} and not func.name.startswith("__"):
                    helper_counts[func.name] = helper_counts.get(func.name, 0) + 1
    common_helpers = sorted([name for name, count in helper_counts.items() if count >= 2])[:5]
    dominant_validation = max(validation_kinds.items(), key=lambda item: item[1])[0] if validation_kinds else ""
    dominant_naming = max(naming_counts.items(), key=lambda item: item[1])[0] if naming_counts else ""
    return {
        "source_root": str(source_root.resolve()),
        "import_scope": scan_summary.get("import_scope") or classify_pattern_import_scope(source_root),
        "collection_name": scan_summary.get("collection_name") or pattern_collection_storage_name(source_root),
        "candidate_count": len(imported_sources),
        "promoted_total": len(promoted_sources),
        "promoted_trusted_count": sum(1 for item in imported_sources if item.get("promoted") and item.get("trust_level") == "trusted"),
        "promoted_experimental_count": sum(1 for item in imported_sources if item.get("promoted") and item.get("trust_level") == "experimental"),
        "blocked_count": sum(1 for item in imported_sources if not item.get("promoted")),
        "file_type_counts": dict(scan_summary.get("file_type_counts") or {}),
        "ignored_count": int(scan_summary.get("ignored_count") or 0),
        "ignored_paths": list(scan_summary.get("ignored_paths") or []),
        "candidate_rel_paths": list(scan_summary.get("candidate_rel_paths") or []),
        "pattern_family_counts": dict(sorted(family_counts.items())),
        "top_directories": dict(sorted(top_directories.items())),
        "common_helpers": common_helpers,
        "validation_kinds": dict(sorted(validation_kinds.items())),
        "dominant_validation_kind": dominant_validation,
        "dominant_naming_style": dominant_naming,
        "source_files": [str(item.get("source_subpath") or "") for item in promoted_sources],
        "tags": list(tags or []),
        "trust_level": trust_level,
    }


def make_collection_pattern_entry(
    source_record: dict,
    pattern_type: str,
    summary: str,
    confidence: float,
    keywords: list[str],
    *,
    normalized_examples: dict | None = None,
    source_files: list[str] | None = None,
) -> dict:
    collection_name = str(source_record.get("collection_name") or "collection")
    pattern_id = re.sub(r"[^a-z0-9_.-]+", "-", f"collection-{collection_name}-{pattern_type}".lower()).strip("-")
    promotion_state = str(source_record.get("effective_promotion_state") or source_record.get("promotion_state_detail") or source_record.get("promotion_state") or "candidate")
    trust_level = str(source_record.get("effective_trust_level") or source_record.get("trust_level") or "trusted")
    return {
        "id": pattern_id,
        "name": pattern_type.replace("_", " "),
        "family": pattern_type,
        "source_files": list(source_files or source_record.get("collection_summary", {}).get("source_files") or []),
        "source_repo_path": str(source_record.get("repo_rel_path") or f"collection/{collection_name}"),
        "source_origin": str(source_record.get("source_origin") or source_record.get("origin_path") or ""),
        "source_subpath": str(source_record.get("source_subpath") or "."),
        "import_scope": str(source_record.get("import_scope") or "folder"),
        "trust_level": trust_level,
        "tags": list(source_record.get("tags") or []),
        "pattern_type": pattern_type,
        "summary": summary,
        "applicability_context": infer_script_pattern_task_tags(pattern_type, summary),
        "confidence": round(confidence, 2),
        "keywords": sorted(set(keywords))[:16],
        "normalized_examples": normalized_examples or {},
        "anti_pattern_note": "",
        "success_count": 0,
        "promotion_state": promotion_state,
        "promotion_method": str(source_record.get("promotion_method") or "automatic"),
        "promotion_reason": str(source_record.get("promotion_reason") or "learned from curated collection source"),
        "timestamp": int(source_record.get("promotion_timestamp") or source_record.get("imported_at") or 0),
    }


def extract_collection_patterns_from_record(source_record: dict) -> list[dict]:
    summary = dict(source_record.get("collection_summary") or {})
    promoted_total = int(summary.get("promoted_total") or 0)
    if promoted_total <= 0:
        return []
    source_files = list(summary.get("source_files") or [])
    collection_name = str(summary.get("collection_name") or source_record.get("collection_name") or "collection")
    keyword_seed = pattern_keywords_from_text(
        " ".join(
            [
                collection_name,
                str(summary.get("source_root") or ""),
                " ".join(source_files[:10]),
                " ".join((summary.get("pattern_family_counts") or {}).keys()),
                " ".join((summary.get("common_helpers") or [])),
            ]
        )
    )
    patterns: list[dict] = []
    if summary.get("top_directories"):
        patterns.append(
            make_collection_pattern_entry(
                source_record,
                "repo_structure",
                f"Collection groups related scripts under {', '.join(list(summary.get('top_directories', {}).keys())[:4])}.",
                0.82,
                keyword_seed + ["layout", "structure", "collection"],
                normalized_examples={"top_directories": summary.get("top_directories")},
                source_files=source_files,
            )
        )
    if summary.get("common_helpers"):
        patterns.append(
            make_collection_pattern_entry(
                source_record,
                "shared_helper_structure",
                f"Collection reuses helper functions such as {', '.join(summary.get('common_helpers', [])[:3])}.",
                0.8,
                keyword_seed + list(summary.get("common_helpers") or []) + ["helpers"],
                normalized_examples={"helpers": summary.get("common_helpers")},
                source_files=source_files,
            )
        )
    dominant_naming = str(summary.get("dominant_naming_style") or "")
    if dominant_naming:
        patterns.append(
            make_collection_pattern_entry(
                source_record,
                "naming_convention",
                f"Collection primarily uses {dominant_naming} module naming.",
                0.76,
                keyword_seed + [dominant_naming, "naming"],
                normalized_examples={"style": dominant_naming},
                source_files=source_files,
            )
        )
    dominant_validation = str(summary.get("dominant_validation_kind") or "")
    if dominant_validation:
        patterns.append(
            make_collection_pattern_entry(
                source_record,
                "validation_strategy",
                f"Collection commonly validates scripts with {dominant_validation}.",
                0.84,
                keyword_seed + [dominant_validation, "validation"],
                normalized_examples={"style": dominant_validation, "validation_kind": dominant_validation},
                source_files=source_files,
            )
        )
    family_counts = dict(summary.get("pattern_family_counts") or {})
    for family, count in sorted(family_counts.items()):
        if int(count or 0) < 2:
            continue
        confidence = min(0.95, 0.72 + (float(count) / float(promoted_total or 1)) * 0.18)
        patterns.append(
            make_collection_pattern_entry(
                source_record,
                str(family),
                f"Across {count} curated files in the collection, {family.replace('_', ' ')} appears as a shared convention.",
                confidence,
                keyword_seed + [str(family), "shared", "collection"],
                source_files=source_files,
            )
        )
    return patterns


def print_pattern_collection_preview(preview: dict) -> None:
    print("=== PATTERN COLLECTION PREVIEW ===")
    print(f"source_root: {preview.get('source_root', '')}")
    print(f"import_scope: {preview.get('import_scope', 'folder')}")
    print(f"candidate_count: {preview.get('candidate_count', 0)}")
    print(f"file_type_counts: {preview.get('file_type_counts', {})}")
    print(f"ignored_count: {preview.get('ignored_count', 0)}")
    if preview.get("ignored_paths"):
        print(f"ignored_paths: {preview.get('ignored_paths')}")
    if preview.get("candidate_rel_paths"):
        print(f"candidate_paths: {preview.get('candidate_rel_paths')[:10]}")
    if preview.get("blocked_reason"):
        print(f"blocked_reason: {preview.get('blocked_reason')}")


def redact_probe_url(raw_url: str) -> tuple[str, bool]:
    text = str(raw_url or "").strip()
    if not text:
        return "", False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text, False
    redacted = False
    hostname = parsed.hostname or ""
    if parsed.username or parsed.password:
        redacted = True
        user = "<REDACTED_USER>"
        pass_part = ":<REDACTED_PASS>" if parsed.password is not None else ""
        hostport = hostname
        if parsed.port is not None:
            hostport = f"{hostname}:{parsed.port}"
        netloc = f"{user}{pass_part}@{hostport}"
    else:
        netloc = parsed.netloc
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    redacted_items: list[tuple[str, str]] = []
    for key, value in query_items:
        if PROBE_SECRET_NAME_RE.search(key):
            redacted_items.append((key, "<REDACTED>"))
            redacted = True
        else:
            redacted_items.append((key, value))
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(redacted_items), parsed.fragment)), redacted


def redact_probe_header_value(name: str, value: str) -> tuple[str, bool]:
    header_name = str(name or "").strip()
    header_value = str(value or "")
    lowered = header_name.lower()
    if lowered == "authorization":
        return "Bearer <REDACTED>", True
    if lowered in {"cookie", "set-cookie"}:
        return "<REDACTED_COOKIE>", True
    if PROBE_SECRET_NAME_RE.search(header_name):
        return "<REDACTED>", True
    redacted_url, changed = redact_probe_url(header_value)
    if changed:
        return redacted_url, True
    token_match = re.search(r"(?i)\b(sk-[A-Za-z0-9]{12,}|gh[pous]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b", header_value)
    if token_match:
        return token_match.re.sub("<REDACTED_TOKEN>", header_value), True
    return header_value, False


def redact_probe_headers(headers: dict[str, str]) -> tuple[dict[str, str], bool]:
    redacted_headers: dict[str, str] = {}
    changed = False
    for key, value in headers.items():
        safe_value, safe_changed = redact_probe_header_value(key, value)
        redacted_headers[str(key)] = safe_value
        changed = changed or safe_changed
    return redacted_headers, changed


def build_probe_proxy_map(http_proxy: str = "", https_proxy: str = "") -> dict[str, str]:
    proxy_map: dict[str, str] = {}
    candidates = {
        "http": http_proxy or CURRENT_SUBPROCESS_ENV.get("HTTP_PROXY") or CURRENT_SUBPROCESS_ENV.get("http_proxy") or os.environ.get("HTTP_PROXY", "") or os.environ.get("http_proxy", ""),
        "https": https_proxy or CURRENT_SUBPROCESS_ENV.get("HTTPS_PROXY") or CURRENT_SUBPROCESS_ENV.get("https_proxy") or os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", ""),
        "all": CURRENT_SUBPROCESS_ENV.get("ALL_PROXY") or CURRENT_SUBPROCESS_ENV.get("all_proxy") or os.environ.get("ALL_PROXY", "") or os.environ.get("all_proxy", ""),
    }
    for scheme, value in candidates.items():
        if value:
            proxy_map[scheme] = value
    return proxy_map


def probe_uses_proxy(url: str, proxy_map: dict[str, str]) -> bool:
    scheme = urlsplit(str(url or "")).scheme.lower()
    return bool(proxy_map.get(scheme) or proxy_map.get("all"))


def parse_probe_header_args(header_args: list[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in header_args or []:
        raw = str(item or "").strip()
        if not raw:
            continue
        if ":" not in raw:
            raise ValueError(f"Invalid --probe-header value: {raw!r}. Expected 'Name: value'.")
        name, value = raw.split(":", 1)
        if not name.strip():
            raise ValueError(f"Invalid --probe-header value: {raw!r}. Header name is required.")
        headers[name.strip()] = value.strip()
    return headers


def build_probe_headers(
    *,
    custom_headers: dict[str, str] | None = None,
    bearer_token: str = "",
    cookies: str = "",
    user_agent: str = "",
) -> tuple[dict[str, str], bool]:
    headers = {str(key): str(value) for key, value in (custom_headers or {}).items()}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if cookies:
        headers["Cookie"] = cookies
    headers.setdefault("User-Agent", user_agent or DEFAULT_PROBE_USER_AGENT)
    _, redacted = redact_probe_headers(headers)
    redacted = redacted or bool(bearer_token) or bool(cookies)
    return headers, redacted


def looks_like_json_body(content_type: str, text: str) -> bool:
    lowered_type = str(content_type or "").lower()
    stripped = str(text or "").lstrip()
    return "json" in lowered_type or stripped.startswith("{") or stripped.startswith("[")


def summarize_json_shape(value: object, depth: int = 0) -> object:
    if depth > 2:
        return type(value).__name__
    if isinstance(value, dict):
        summary: dict[str, object] = {}
        for key in list(value.keys())[:8]:
            summary[str(key)] = summarize_json_shape(value[key], depth + 1)
        return summary
    if isinstance(value, list):
        if not value:
            return ["empty"]
        return [summarize_json_shape(value[0], depth + 1)]
    return type(value).__name__


def classify_uri_reference_mode(uris: list[str]) -> str:
    if not uris:
        return "none"
    has_absolute = any(urlparse(item).scheme in {"http", "https"} for item in uris)
    has_relative = any(urlparse(item).scheme not in {"http", "https"} for item in uris)
    if has_absolute and has_relative:
        return "mixed"
    if has_absolute:
        return "absolute"
    return "relative"


class ProbeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.redirect_chain: list[dict] = []

    def _record(self, req: urllib.request.Request, code: int, headers) -> None:
        location = headers.get("Location", "")
        safe_from, _ = redact_probe_url(req.full_url)
        safe_to, _ = redact_probe_url(urljoin(req.full_url, location))
        self.redirect_chain.append(
            {
                "status_code": int(code),
                "from": safe_from,
                "to": safe_to,
            }
        )

    def http_error_301(self, req, fp, code, msg, headers):
        self._record(req, code, headers)
        return super().http_error_301(req, fp, code, msg, headers)

    def http_error_302(self, req, fp, code, msg, headers):
        self._record(req, code, headers)
        return super().http_error_302(req, fp, code, msg, headers)

    def http_error_303(self, req, fp, code, msg, headers):
        self._record(req, code, headers)
        return super().http_error_303(req, fp, code, msg, headers)

    def http_error_307(self, req, fp, code, msg, headers):
        self._record(req, code, headers)
        return super().http_error_307(req, fp, code, msg, headers)

    def http_error_308(self, req, fp, code, msg, headers):
        self._record(req, code, headers)
        return super().http_error_308(req, fp, code, msg, headers)


def bounded_http_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_PROBE_MAX_BYTES,
    http_proxy: str = "",
    https_proxy: str = "",
) -> dict:
    proxy_map = build_probe_proxy_map(http_proxy=http_proxy, https_proxy=https_proxy)
    redirect_handler = ProbeRedirectHandler()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_map), redirect_handler)
    request = urllib.request.Request(url, headers=headers or {}, method=method.upper())
    result = {
        "ok": False,
        "status_code": 0,
        "content_type": "",
        "headers": {},
        "body_text": "",
        "body_bytes": b"",
        "truncated": False,
        "redirect_chain": [],
        "redirected": False,
        "final_url": url,
        "proxy_used": probe_uses_proxy(url, proxy_map),
        "proxy_map": proxy_map,
        "error": "",
        "timed_out": False,
    }
    try:
        with opener.open(request, timeout=max(1, int(timeout_seconds))) as response:
            body_bytes = response.read(max_bytes + 1)
            truncated = len(body_bytes) > max_bytes
            if truncated:
                body_bytes = body_bytes[:max_bytes]
            result.update(
                {
                    "ok": True,
                    "status_code": int(getattr(response, "status", response.getcode())),
                    "content_type": str(response.headers.get("Content-Type", "")),
                    "headers": {str(key): str(value) for key, value in response.headers.items()},
                    "body_bytes": body_bytes,
                    "body_text": body_bytes.decode("utf-8", errors="replace"),
                    "truncated": truncated,
                    "final_url": response.geturl(),
                }
            )
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read(max_bytes + 1)
        truncated = len(body_bytes) > max_bytes
        if truncated:
            body_bytes = body_bytes[:max_bytes]
        result.update(
            {
                "ok": True,
                "status_code": int(exc.code),
                "content_type": str(exc.headers.get("Content-Type", "")),
                "headers": {str(key): str(value) for key, value in exc.headers.items()},
                "body_bytes": body_bytes,
                "body_text": body_bytes.decode("utf-8", errors="replace"),
                "truncated": truncated,
                "final_url": exc.geturl() or url,
                "error": str(exc),
            }
        )
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        message = str(getattr(exc, "reason", exc) or exc)
        result["error"] = message
        result["timed_out"] = "timed out" in message.lower()
    result["redirect_chain"] = redirect_handler.redirect_chain
    result["redirected"] = bool(redirect_handler.redirect_chain)
    return result


def summarize_rate_limit_headers(headers: dict[str, str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered.startswith("x-ratelimit") or lowered in {"ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "retry-after"}:
            selected[key] = value
    return selected


def analyze_api_probe_response(fetch_result: dict) -> dict:
    body_text = str(fetch_result.get("body_text") or "")
    content_type = str(fetch_result.get("content_type") or "")
    is_json = looks_like_json_body(content_type, body_text)
    json_summary: dict[str, object] = {
        "body_is_json": False,
        "json_top_level_keys": [],
        "json_shape": {},
    }
    if is_json:
        try:
            parsed = json.loads(body_text)
            json_summary["body_is_json"] = True
            if isinstance(parsed, dict):
                json_summary["json_top_level_keys"] = list(parsed.keys())[:10]
            json_summary["json_shape"] = summarize_json_shape(parsed)
        except json.JSONDecodeError:
            pass
    status_code = int(fetch_result.get("status_code") or 0)
    auth_hint = ""
    if status_code == 401:
        auth_hint = "authentication appears required or the bearer token was rejected"
    elif status_code == 403:
        auth_hint = "request was forbidden; auth, origin, or anti-bot checks may apply"
    elif status_code == 407:
        auth_hint = "proxy authentication appears required"
    summary_bits = [
        f"status={status_code or 'unreachable'}",
        f"content_type={content_type or 'unknown'}",
    ]
    if json_summary["body_is_json"]:
        summary_bits.append("json body detected")
    if auth_hint:
        summary_bits.append(auth_hint)
    return {
        **json_summary,
        "rate_limit_headers": summarize_rate_limit_headers(fetch_result.get("headers") or {}),
        "auth_failure_hint": auth_hint,
        "summary": "; ".join(summary_bits),
    }


def parse_m3u8_attributes(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', str(text or "")):
        value = match.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        attrs[match.group(1)] = value
    return attrs


def analyze_m3u8_playlist(
    playlist_url: str,
    playlist_text: str,
    *,
    request_headers: dict[str, str] | None = None,
    timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_PROBE_MAX_BYTES,
    follow_up_limit: int = DEFAULT_PROBE_FOLLOW_UP_LIMIT,
    http_proxy: str = "",
    https_proxy: str = "",
) -> dict:
    stripped = str(playlist_text or "").strip()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    is_valid = bool(lines and lines[0].startswith("#EXTM3U"))
    variant_uris: list[str] = []
    segment_uris: list[str] = []
    audio_groups: list[str] = []
    subtitle_groups: list[str] = []
    playlist_type = "unknown"
    target_duration = None
    media_sequence = None
    key_tags_present = False
    pending_variant = False
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            pending_variant = True
            attrs = parse_m3u8_attributes(line.partition(":")[2])
            if attrs.get("AUDIO"):
                audio_groups.append(attrs["AUDIO"])
            if attrs.get("SUBTITLES"):
                subtitle_groups.append(attrs["SUBTITLES"])
            continue
        if line.startswith("#EXT-X-TARGETDURATION"):
            try:
                target_duration = int(line.partition(":")[2].strip())
            except ValueError:
                target_duration = None
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE"):
            try:
                media_sequence = int(line.partition(":")[2].strip())
            except ValueError:
                media_sequence = None
            continue
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = parse_m3u8_attributes(line.partition(":")[2])
            media_type = attrs.get("TYPE", "").upper()
            if media_type == "AUDIO" and attrs.get("GROUP-ID"):
                audio_groups.append(attrs["GROUP-ID"])
            if media_type == "SUBTITLES" and attrs.get("GROUP-ID"):
                subtitle_groups.append(attrs["GROUP-ID"])
            continue
        if line.startswith("#EXT-X-KEY"):
            key_tags_present = True
            continue
        if line.startswith("#"):
            continue
        if pending_variant:
            variant_uris.append(line)
            pending_variant = False
        else:
            segment_uris.append(line)
    if variant_uris:
        playlist_type = "master"
    elif any(line.startswith("#EXTINF") for line in lines) or segment_uris:
        playlist_type = "media"
    if not is_valid:
        playlist_type = "unknown"
    follow_up_results: list[dict] = []
    sample_targets = variant_uris if playlist_type == "master" else segment_uris
    for rel_uri in sample_targets[: max(0, min(int(follow_up_limit), DEFAULT_PROBE_FOLLOW_UP_LIMIT))]:
        absolute = urljoin(playlist_url, rel_uri)
        follow = bounded_http_fetch(
            absolute,
            method="HEAD",
            headers=request_headers,
            timeout_seconds=timeout_seconds,
            max_bytes=min(max_bytes, 2048),
            http_proxy=http_proxy,
            https_proxy=https_proxy,
        )
        safe_url, _ = redact_probe_url(absolute)
        follow_up_results.append(
            {
                "url": safe_url,
                "ok": bool(follow.get("ok")),
                "status_code": int(follow.get("status_code") or 0),
                "redirected": bool(follow.get("redirected")),
            }
        )
    uri_reference_mode = classify_uri_reference_mode(variant_uris if playlist_type == "master" else segment_uris)
    return {
        "valid_playlist": is_valid,
        "playlist_type": playlist_type if playlist_type in {"master", "media"} else "unknown",
        "variant_count": len(variant_uris),
        "sample_variant_uris": [redact_probe_url(urljoin(playlist_url, item))[0] for item in variant_uris[:3]],
        "audio_group_references": sorted(set(audio_groups)),
        "subtitle_group_references": sorted(set(subtitle_groups)),
        "target_duration": target_duration,
        "media_sequence": media_sequence,
        "segment_count": len(segment_uris),
        "segment_sample_count": min(len(segment_uris), 3),
        "sample_segment_uris": [redact_probe_url(urljoin(playlist_url, item))[0] for item in segment_uris[:3]],
        "key_tags_present": key_tags_present,
        "uri_reference_mode": uri_reference_mode,
        "sample_uri_probe_results": follow_up_results,
        "summary": (
            f"playlist_type={playlist_type if is_valid else 'invalid'}; "
            f"variants={len(variant_uris)}; segments={len(segment_uris)}; "
            f"keys_present={str(key_tags_present).lower()}"
        ),
    }


def determine_probe_mode(endpoint: str, requested_mode: str = "auto") -> str:
    mode = str(requested_mode or "auto").strip().lower()
    if mode and mode != "auto":
        return mode
    parsed = urlparse(str(endpoint or ""))
    path = parsed.path.lower()
    if path.endswith(".m3u8"):
        return "m3u8_summary"
    return "json_summary"


def probe_endpoint(
    endpoint: str,
    *,
    probe_type: str = "auto",
    method: str = "",
    custom_headers: dict[str, str] | None = None,
    bearer_token: str = "",
    cookies: str = "",
    user_agent: str = "",
    http_proxy: str = "",
    https_proxy: str = "",
    timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_PROBE_MAX_BYTES,
    follow_up_limit: int = DEFAULT_PROBE_FOLLOW_UP_LIMIT,
) -> dict:
    resolved_probe_type = determine_probe_mode(endpoint, probe_type)
    headers, header_redacted = build_probe_headers(
        custom_headers=custom_headers,
        bearer_token=bearer_token,
        cookies=cookies,
        user_agent=user_agent,
    )
    request_method = (method or ("HEAD" if resolved_probe_type in {"head", "headers_summary"} else "GET")).upper()
    fetch_result = bounded_http_fetch(
        endpoint,
        method=request_method,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
    )
    safe_endpoint, endpoint_redacted = redact_probe_url(endpoint)
    safe_final_url, final_redacted = redact_probe_url(str(fetch_result.get("final_url") or endpoint))
    redacted_headers, header_values_redacted = redact_probe_headers(fetch_result.get("headers") or {})
    proxy_issue = "proxy" in str(fetch_result.get("error") or "").lower() or int(fetch_result.get("status_code") or 0) == 407
    result = {
        "probe_type": resolved_probe_type,
        "endpoint": safe_endpoint,
        "method": request_method,
        "status_code": int(fetch_result.get("status_code") or 0),
        "content_type": str(fetch_result.get("content_type") or ""),
        "redirected": bool(fetch_result.get("redirected")),
        "redirect_chain": fetch_result.get("redirect_chain") or [],
        "final_url": safe_final_url,
        "proxy_used": bool(fetch_result.get("proxy_used")),
        "proxy_likely_worked": bool(fetch_result.get("proxy_used")) and bool(fetch_result.get("ok")),
        "redactions_applied": bool(header_redacted or endpoint_redacted or final_redacted or header_values_redacted),
        "response_headers": redacted_headers,
        "ok": bool(fetch_result.get("ok")),
        "timed_out": bool(fetch_result.get("timed_out")),
        "truncated": bool(fetch_result.get("truncated")),
        "summary": "",
        "confidence": "low",
        "probe_confidence": "low",
        "error": str(fetch_result.get("error") or ""),
        "auth_failure_hint": "",
        "rate_limit_headers": {},
    }
    if not fetch_result.get("ok"):
        error_text = str(fetch_result.get("error") or "request failed")
        result["summary"] = error_text
        result["auth_failure_hint"] = "proxy/auth issue likely" if proxy_issue else ""
        return result
    if resolved_probe_type in {"json_summary", "get", "headers_summary", "head"}:
        api_summary = analyze_api_probe_response(fetch_result)
        result["body_is_json"] = bool(api_summary.get("body_is_json"))
        result["json_top_level_keys"] = api_summary.get("json_top_level_keys") or []
        result["json_shape"] = api_summary.get("json_shape") or {}
        result["auth_failure_hint"] = str(api_summary.get("auth_failure_hint") or "")
        result["rate_limit_headers"] = api_summary.get("rate_limit_headers") or {}
        result["summary"] = str(api_summary.get("summary") or "")
        result["confidence"] = "high" if result.get("body_is_json") or result["status_code"] in {200, 204} else "medium"
    elif resolved_probe_type == "m3u8_summary":
        playlist_info = analyze_m3u8_playlist(
            endpoint,
            str(fetch_result.get("body_text") or ""),
            request_headers=headers,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            follow_up_limit=follow_up_limit,
            http_proxy=http_proxy,
            https_proxy=https_proxy,
        )
        result.update(playlist_info)
        if result.get("status_code") in {401, 403, 407}:
            result["auth_failure_hint"] = "playlist fetch suggests auth or proxy restrictions"
        result["confidence"] = "high" if playlist_info.get("valid_playlist") else "medium"
        result["summary"] = str(playlist_info.get("summary") or "")
    result["probe_confidence"] = result["confidence"]
    return result


def print_probe_result(probe: dict, output_format: str = "human") -> None:
    if output_format == "json":
        print(json.dumps(probe, indent=2, sort_keys=True))
        return
    print("=== NETWORK PROBE ===")
    print(f"probe_type: {probe.get('probe_type') or 'get'}")
    print(f"endpoint: {probe.get('endpoint') or '(none)'}")
    print(f"method: {probe.get('method') or 'GET'}")
    print(f"status_code: {probe.get('status_code') or 0}")
    print(f"content_type: {probe.get('content_type') or '(unknown)'}")
    print(f"redirected: {format_bool(probe.get('redirected'))}")
    print(f"proxy_used: {format_bool(probe.get('proxy_used'))}")
    print(f"proxy_likely_worked: {format_bool(probe.get('proxy_likely_worked'))}")
    print(f"redactions_applied: {format_bool(probe.get('redactions_applied'))}")
    print(f"probe_confidence: {probe.get('probe_confidence') or probe.get('confidence') or 'low'}")
    print(f"summary: {probe.get('summary') or '(none)'}")
    if probe.get("error"):
        print(f"error: {probe.get('error')}")
    if probe.get("redirect_chain"):
        print(f"redirect_chain: {probe.get('redirect_chain')}")
    if probe.get("rate_limit_headers"):
        print(f"rate_limit_headers: {probe.get('rate_limit_headers')}")
    if probe.get("auth_failure_hint"):
        print(f"auth_failure_hint: {probe.get('auth_failure_hint')}")
    if probe.get("probe_type") in {"head", "headers_summary"} and probe.get("response_headers"):
        print(f"response_headers: {probe.get('response_headers')}")
    if probe.get("probe_type") == "m3u8_summary":
        print(f"valid_playlist: {format_bool(probe.get('valid_playlist'))}")
        print(f"playlist_type: {probe.get('playlist_type') or 'unknown'}")
        print(f"variant_count: {probe.get('variant_count') or 0}")
        print(f"sample_variant_uris: {probe.get('sample_variant_uris') or []}")
        print(f"audio_group_references: {probe.get('audio_group_references') or []}")
        print(f"subtitle_group_references: {probe.get('subtitle_group_references') or []}")
        print(f"target_duration: {probe.get('target_duration')}")
        print(f"media_sequence: {probe.get('media_sequence')}")
        print(f"segment_count: {probe.get('segment_count') or 0}")
        print(f"segment_sample_count: {probe.get('segment_sample_count') or 0}")
        print(f"sample_segment_uris: {probe.get('sample_segment_uris') or []}")
        print(f"key_tags_present: {format_bool(probe.get('key_tags_present'))}")
        print(f"uri_reference_mode: {probe.get('uri_reference_mode') or 'none'}")
        print(f"sample_uri_probe_results: {probe.get('sample_uri_probe_results') or []}")
    else:
        print(f"body_is_json: {format_bool(probe.get('body_is_json'))}")
        print(f"json_top_level_keys: {probe.get('json_top_level_keys') or []}")
        print(f"json_shape: {probe.get('json_shape') or {}}")


def run_candidate_validation(pattern_repo: Path, candidate_path: Path) -> dict:
    plan = build_script_validation_plan(pattern_repo, candidate_path)
    result = run_validation_stack(pattern_repo, plan)
    return {
        "plan": plan,
        "result": result,
        "passed": bool(result.get("ok")),
        "limited_validation": bool(plan.get("limited_validation") or plan.get("only_syntax_import_validation")),
        "validation_command": plan.get("primary_command", ""),
    }


def repair_training_candidate(pattern_repo: Path, candidate_path: Path) -> dict:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--script",
        str(candidate_path),
        "--dry-run",
        "--no-publish-on-success",
    ]
    code, output = run_subprocess(command, pattern_repo)
    return {"ok": code == 0, "output": output.strip(), "command": command}


def import_pattern_files(
    pattern_repo: Path,
    file_paths: list[str],
    trust_level: str = "trusted",
    tags: list[str] | None = None,
    note: str = "",
) -> dict:
    trust = trust_level if trust_level in PATTERN_TRUST_LEVELS else "trusted"
    repo_root, created_repo = ensure_pattern_repo_status(pattern_repo)
    catalog = load_pattern_source_catalog(repo_root)
    imported: list[dict] = []
    sources = [item for item in catalog.get("sources", []) if isinstance(item, dict)]
    existing_by_id = {item.get("id", ""): item for item in sources}
    pattern_count_before = len(load_script_pattern_memory(repo_root).get("patterns", []))
    for file_value in file_paths:
        record = import_pattern_source_record(
            repo_root,
            file_value,
            trust_level=trust,
            tags=tags,
            note=note,
            source_metadata={"import_scope": "file"},
        )
        existing_by_id[record["id"]] = record
        imported.append(record)
    catalog["sources"] = sorted(existing_by_id.values(), key=lambda item: item.get("repo_rel_path", ""))
    save_pattern_source_catalog(repo_root, catalog)
    promoted_trusted = any(item.get("promoted") and item.get("trust_level") == "trusted" for item in imported)
    learn_result = relearn_patterns_from_repo(repo_root) if promoted_trusted else {"learned_patterns": [], "memory": load_script_pattern_memory(repo_root)}
    return {
        "pattern_repo": str(repo_root),
        "created_repo": created_repo,
        "imported_sources": imported,
        "learned_patterns": learn_result.get("learned_patterns", []),
        "learned_pattern_delta": len(learn_result.get("memory", {}).get("patterns", [])) - pattern_count_before,
        "relearn_triggered": promoted_trusted,
    }


def import_pattern_repo_collection(
    pattern_repo: Path,
    source_root: Path | str,
    *,
    trust_level: str = "trusted",
    tags: list[str] | None = None,
    note: str = "",
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
    max_files: int = DEFAULT_PATTERN_REPO_MAX_FILES,
    max_depth: int = 0,
) -> dict:
    trust = trust_level if trust_level in PATTERN_TRUST_LEVELS else "trusted"
    repo_root, created_repo = ensure_pattern_repo_status(pattern_repo)
    source_path = Path(source_root).expanduser().resolve()
    preview = scan_pattern_source_collection(
        source_path,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_files=max_files,
        max_depth=max_depth,
    )
    if not preview.get("ok"):
        return {
            "pattern_repo": str(repo_root),
            "created_repo": created_repo,
            "import_scope": preview.get("import_scope") or "folder",
            "source_root": str(source_path),
            "candidate_count": 0,
            "imported_sources": [],
            "repo_level_patterns_added": 0,
            "pattern_memory_delta": 0,
            "promoted_trusted_count": 0,
            "promoted_experimental_count": 0,
            "blocked_count": 0,
            "preview": preview,
            "blocked_reason": preview.get("blocked_reason") or "collection scan failed",
            "relearn_triggered": False,
        }
    catalog = load_pattern_source_catalog(repo_root)
    existing_by_id = {item.get("id", ""): item for item in catalog.get("sources", []) if isinstance(item, dict)}
    pattern_count_before = len(load_script_pattern_memory(repo_root).get("patterns", []))
    collection_name = str(preview.get("collection_name") or pattern_collection_storage_name(source_path))
    imported: list[dict] = []
    for absolute, rel_text in zip(preview.get("candidate_paths", []), preview.get("candidate_rel_paths", [])):
        candidate_path = infer_pattern_collection_candidate_destination(repo_root, collection_name, rel_text)
        curated_path = infer_pattern_collection_curated_destination(repo_root, collection_name, rel_text, trust)
        record = import_pattern_source_record(
            repo_root,
            absolute,
            trust_level=trust,
            tags=tags,
            note=note,
            candidate_path=candidate_path,
            curated_path=curated_path,
            source_metadata={
                "import_scope": preview.get("import_scope") or "folder",
                "source_repo_path": str(source_path),
                "source_subpath": rel_text,
                "source_kind": "file",
                "collection_name": collection_name,
                "id": pattern_repo_source_id(f"{collection_name}:{rel_text}", trust),
            },
        )
        existing_by_id[record["id"]] = record
        imported.append(record)
    collection_summary = build_pattern_collection_summary(
        repo_root,
        source_path,
        preview,
        imported,
        trust_level=trust,
        tags=tags,
    )
    collection_record = {
        "id": pattern_repo_source_id(f"collection:{collection_name}", trust),
        "repo_rel_path": f"collection/{collection_name}",
        "candidate_path": "",
        "origin_path": str(source_path),
        "source_type": "local",
        "source_origin": str(source_path),
        "acquisition_method": "scan",
        "proxy_used": False,
        "sanitized_path": "",
        "trust_level": trust,
        "tags": list(tags or []),
        "note": note,
        "imported_at": int(time.time()),
        "sanitized_changed": False,
        "sanitization_applied": False,
        "validation_status": "passed" if collection_summary.get("promoted_total") else "blocked",
        "validation_passed": bool(collection_summary.get("promoted_total")),
        "validation_command": "",
        "repair_needed": False,
        "repair_output": "",
        "promotion_state": "curated" if collection_summary.get("promoted_total") else "candidate",
        "promotion_state_detail": (f"curated_{trust}" if collection_summary.get("promoted_total") else "candidate"),
        "promoted": bool(collection_summary.get("promoted_total")),
        "candidate_imported": False,
        "limited_validation": False,
        "blocked_reason": "" if collection_summary.get("promoted_total") else "no curated files were promoted from the collection",
        "final_destination": "",
        "import_scope": str(collection_summary.get("import_scope") or preview.get("import_scope") or "folder"),
        "source_repo_path": str(source_path),
        "source_subpath": ".",
        "source_kind": "collection",
        "collection_name": collection_name,
        "collection_summary": collection_summary,
    }
    repo_level_patterns = extract_collection_patterns_from_record(collection_record)
    existing_by_id[collection_record["id"]] = collection_record
    catalog["sources"] = sorted(existing_by_id.values(), key=lambda item: item.get("repo_rel_path", ""))
    save_pattern_source_catalog(repo_root, catalog)
    relearn_triggered = bool(imported)
    learn_result = relearn_patterns_from_repo(repo_root) if relearn_triggered else {"learned_patterns": [], "memory": load_script_pattern_memory(repo_root)}
    memory_delta = len(learn_result.get("memory", {}).get("patterns", [])) - pattern_count_before
    return {
        "pattern_repo": str(repo_root),
        "created_repo": created_repo,
        "import_scope": preview.get("import_scope") or "folder",
        "source_root": str(source_path),
        "candidate_count": len(imported),
        "imported_sources": imported,
        "preview": preview,
        "collection_summary": collection_summary,
        "repo_level_patterns_added": len(repo_level_patterns),
        "pattern_memory_delta": memory_delta,
        "learned_patterns": learn_result.get("learned_patterns", []),
        "learned_pattern_delta": memory_delta,
        "promoted_trusted_count": collection_summary.get("promoted_trusted_count", 0),
        "promoted_experimental_count": collection_summary.get("promoted_experimental_count", 0),
        "blocked_count": collection_summary.get("blocked_count", 0),
        "relearn_triggered": relearn_triggered,
        "blocked_reason": "",
    }


def scan_pattern_repo_sources(pattern_repo: Path) -> list[dict]:
    repo_root = ensure_pattern_repo(pattern_repo)
    catalog = load_pattern_source_catalog(repo_root)
    sources = [
        item for item in catalog.get("sources", [])
        if isinstance(item, dict) and str(item.get("promotion_state", "")) == "curated"
    ]
    if sources:
        return sources
    discovered: list[dict] = []
    for trust_level in sorted(PATTERN_TRUST_LEVELS):
        root = repo_root / "curated" / trust_level
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            discovered.append(
                {
                    "id": pattern_repo_source_id(path.relative_to(repo_root), trust_level),
                    "repo_rel_path": str(path.relative_to(repo_root)),
                    "origin_path": "",
                    "sanitized_path": str(path),
                    "trust_level": trust_level,
                    "tags": [],
                    "note": "",
                    "imported_at": 0,
                    "validation_status": "passed",
                    "repair_needed": False,
                    "promotion_state": "curated",
                    "sanitized_changed": False,
                }
            )
    if discovered:
        save_pattern_source_catalog(repo_root, {"sources": discovered})
    return discovered


def relearn_patterns_from_repo(pattern_repo: Path) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    memory = {"patterns": [], "sources": [], "updated_at": 0}
    learned_patterns: list[dict] = []
    for source_record in scan_pattern_repo_sources(repo_root):
        if str(source_record.get("source_kind") or "") == "collection":
            learned_patterns.extend(extract_collection_patterns_from_record(source_record))
            continue
        repo_rel = str(source_record.get("repo_rel_path") or "").strip()
        if not repo_rel:
            continue
        script_path = repo_root / repo_rel
        if not script_path.exists() or not script_path.is_file():
            continue
        learned_patterns.extend(extract_script_patterns_with_metadata(repo_root, script_path, source_record))
    memory = upsert_script_patterns(memory, learned_patterns)
    save_script_pattern_memory(repo_root, memory)
    return {
        "pattern_repo": str(repo_root),
        "learned_patterns": learned_patterns,
        "memory": memory,
    }


def list_pattern_sources(pattern_repo: Path) -> list[dict]:
    return scan_pattern_repo_sources(pattern_repo)


def list_patterns(pattern_repo: Path) -> list[dict]:
    memory = load_script_pattern_memory(pattern_repo)
    return [item for item in memory.get("patterns", []) if isinstance(item, dict)]


def normalize_pattern_promotion_state(source_record: dict) -> str:
    state = str(source_record.get("promotion_state") or "").strip().lower()
    trust_level = str(source_record.get("trust_level") or "trusted").strip().lower()
    if state == "candidate":
        return "candidate"
    if state == "curated":
        return "curated_trusted" if trust_level == "trusted" else "curated_experimental"
    if state in {"curated_trusted", "curated_experimental"}:
        return state
    return "candidate"


def normalize_pattern_validation_result(source_record: dict) -> str:
    raw = str(source_record.get("validation_result") or source_record.get("validation_status") or "").strip().lower()
    if raw in {"passed", "success"}:
        return "success"
    if raw in {"blocked", "failed"}:
        return raw
    return "not_recorded"


def derive_pattern_promotion_reason(source_record: dict) -> str:
    if source_record.get("promotion_reason"):
        return str(source_record.get("promotion_reason"))
    promotion_state = str(source_record.get("effective_promotion_state") or normalize_pattern_promotion_state(source_record))
    validation_result = normalize_pattern_validation_result(source_record)
    if promotion_state == "candidate":
        if source_record.get("limited_validation"):
            return "validation limited; retained as candidate"
        blocked_reason = str(source_record.get("blocked_reason") or "").strip()
        if blocked_reason:
            return blocked_reason
        if validation_result == "failed":
            return "validation failed; not promoted"
        if validation_result == "blocked":
            return "validation blocked; not promoted"
        return "candidate import awaiting curation"
    if promotion_state == "curated_experimental":
        return "validated and curated as experimental"
    return "validated and curated as trusted"


def enrich_pattern_entry(pattern: dict, source_record: dict | None = None) -> dict:
    source_record = source_record or {}
    source_file = str(
        source_record.get("repo_rel_path")
        or source_record.get("candidate_path")
        or pattern.get("source_repo_path")
        or (pattern.get("source_files") or [""])[0]
        or ""
    )
    return {
        "id": str(pattern.get("id") or ""),
        "pattern_type": str(pattern.get("pattern_type") or ""),
        "source_file": source_file,
        "source_origin": str(source_record.get("source_origin") or source_record.get("origin_path") or pattern.get("source_origin") or ""),
        "source_type": str(source_record.get("source_type") or ""),
        "trust_level": str(pattern.get("trust_level") or source_record.get("effective_trust_level") or source_record.get("trust_level") or "trusted"),
        "promotion_state": str(
            pattern.get("promotion_state")
            or source_record.get("effective_promotion_state")
            or source_record.get("promotion_state")
            or ("curated_trusted" if str(pattern.get("trust_level") or "trusted") == "trusted" else "curated_experimental")
        ),
        "tags": sorted(set(list(source_record.get("tags") or []) + list(pattern.get("tags") or []))),
        "applicability_context": list(pattern.get("applicability_context") or []),
        "confidence": float(pattern.get("confidence", 0) or 0),
        "validation_result": normalize_pattern_validation_result(source_record) if source_record else "success",
        "publish_result": str(source_record.get("publish_result") or "not_recorded"),
        "regression_status": str(source_record.get("regression_status") or "none"),
        "last_validated_commit": str(source_record.get("last_validated_commit") or source_record.get("last_validated_fingerprint") or ""),
        "last_published_commit": str(source_record.get("last_published_commit") or ""),
        "pr_url": str(source_record.get("pr_url") or ""),
        "promotion_reason": str(pattern.get("promotion_reason") or (derive_pattern_promotion_reason(source_record) if source_record else "learned from curated source")),
        "timestamp": int(pattern.get("timestamp") or source_record.get("imported_at") or 0),
        "promotion_method": str(pattern.get("promotion_method") or source_record.get("promotion_method") or "automatic"),
        "summary": str(pattern.get("summary") or ""),
        "anti_pattern_note": str(pattern.get("anti_pattern_note") or ""),
        "source_repo_path": str(pattern.get("source_repo_path") or source_file),
        "source_subpath": str(pattern.get("source_subpath") or source_record.get("source_subpath") or ""),
        "import_scope": str(pattern.get("import_scope") or source_record.get("import_scope") or "file"),
        "source_files": list(pattern.get("source_files") or []),
        "keywords": list(pattern.get("keywords") or []),
        "family": str(pattern.get("family") or pattern.get("pattern_type") or ""),
    }


def build_pattern_inspection_records(pattern_repo: Path) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    catalog = {"sources": load_effective_pattern_sources(repo_root)}
    memory = load_script_pattern_memory(repo_root)
    patterns = [item for item in memory.get("patterns", []) if isinstance(item, dict)]
    source_records = [item for item in catalog.get("sources", []) if isinstance(item, dict)]
    source_by_repo_rel = {
        str(item.get("repo_rel_path") or item.get("candidate_path") or ""): item
        for item in source_records
        if str(item.get("repo_rel_path") or item.get("candidate_path") or "")
    }
    records: list[dict] = []
    seen_ids: set[str] = set()
    for pattern in patterns:
        key = str(pattern.get("source_repo_path") or "")
        source_record = source_by_repo_rel.get(key)
        enriched = enrich_pattern_entry(pattern, source_record)
        seen_ids.add(enriched["id"])
        records.append(enriched)
    for source_record in source_records:
        if normalize_pattern_promotion_state(source_record) != "candidate":
            continue
        candidate_rel = str(source_record.get("candidate_path") or source_record.get("repo_rel_path") or "").strip()
        if not candidate_rel:
            continue
        candidate_path = repo_root / candidate_rel
        if not candidate_path.exists() or not candidate_path.is_file():
            continue
        for pattern in extract_script_patterns_with_metadata(repo_root, candidate_path, source_record):
            enriched = enrich_pattern_entry(pattern, source_record)
            if enriched["id"] in seen_ids:
                continue
            seen_ids.add(enriched["id"])
            records.append(enriched)
    return {
        "pattern_repo": str(repo_root),
        "memory_updated_at": int(memory.get("updated_at", 0) or 0),
        "patterns": sorted(records, key=lambda item: (item.get("promotion_state", ""), item.get("id", ""))),
        "sources": source_records,
    }


def filter_pattern_inspection_records(
    records: list[dict],
    *,
    filter_state: str = "",
    filter_tag: str = "",
    search: str = "",
    limit: int = 0,
) -> list[dict]:
    selected = list(records)
    if filter_state:
        selected = [item for item in selected if str(item.get("promotion_state") or "") == filter_state]
    if filter_tag:
        tag = filter_tag.strip().lower()
        selected = [item for item in selected if tag in {str(entry).strip().lower() for entry in item.get("tags", [])}]
    if search:
        needle = search.strip().lower()
        selected = [
            item
            for item in selected
            if needle in " ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("pattern_type") or ""),
                    str(item.get("source_file") or ""),
                    str(item.get("source_origin") or ""),
                    " ".join(str(tag) for tag in item.get("tags", [])),
                    " ".join(str(ctx) for ctx in item.get("applicability_context", [])),
                    str(item.get("promotion_reason") or ""),
                    str(item.get("summary") or ""),
                ]
            ).lower()
        ]
    if limit > 0:
        selected = selected[:limit]
    return selected


def summarize_pattern_records(records: list[dict]) -> dict:
    summary = {
        "total_patterns": len(records),
        "curated_trusted": 0,
        "curated_experimental": 0,
        "candidate": 0,
        "trusted_count": 0,
        "experimental_count": 0,
        "candidate_count": 0,
    }
    for item in records:
        state = str(item.get("promotion_state") or "")
        trust = str(item.get("trust_level") or "")
        if state in {"curated_trusted", "curated_experimental", "candidate"}:
            summary[state] += 1
        if state == "candidate":
            summary["candidate_count"] += 1
        elif trust == "trusted":
            summary["trusted_count"] += 1
        else:
            summary["experimental_count"] += 1
    return summary


def inspect_patterns(
    pattern_repo: Path | None,
    *,
    filter_state: str = "",
    filter_tag: str = "",
    search: str = "",
    limit: int = 0,
) -> dict:
    if pattern_repo is None:
        return {
            "pattern_repo": "none",
            "summary": summarize_pattern_records([]),
            "patterns": [],
            "memory_updated_at": 0,
        }
    inspection = build_pattern_inspection_records(pattern_repo)
    filtered = filter_pattern_inspection_records(
        inspection.get("patterns", []),
        filter_state=filter_state,
        filter_tag=filter_tag,
        search=search,
        limit=limit,
    )
    return {
        "pattern_repo": inspection.get("pattern_repo", "none"),
        "summary": summarize_pattern_records(filtered),
        "patterns": filtered,
        "memory_updated_at": inspection.get("memory_updated_at", 0),
    }


def inspect_pattern_sources(pattern_repo: Path | None, *, limit: int = 0, filter_state: str = "", filter_tag: str = "", search: str = "") -> dict:
    if pattern_repo is None:
        return {
            "pattern_repo": "none",
            "summary": {"total_sources": 0, "curated_trusted": 0, "curated_experimental": 0, "candidate": 0},
            "sources": [],
        }
    repo_root = ensure_pattern_repo(pattern_repo)
    catalog = {"sources": load_effective_pattern_sources(repo_root)}
    memory = load_script_pattern_memory(repo_root)
    counts_by_source: dict[str, int] = {}
    for pattern in memory.get("patterns", []):
        if not isinstance(pattern, dict):
            continue
        source_key = str(pattern.get("source_repo_path") or "")
        if source_key:
            counts_by_source[source_key] = counts_by_source.get(source_key, 0) + 1
    sources: list[dict] = []
    for source in [item for item in catalog.get("sources", []) if isinstance(item, dict)]:
        promotion_state = normalize_pattern_promotion_state(source)
        entry = {
            "path": str(source.get("repo_rel_path") or source.get("candidate_path") or ""),
            "source_origin": str(source.get("source_origin") or source.get("origin_path") or ""),
            "trust_level": str(source.get("effective_trust_level") or source.get("trust_level") or "trusted"),
            "promotion_state": str(source.get("effective_promotion_state") or promotion_state),
            "tags": list(source.get("tags") or []),
            "validation_result": normalize_pattern_validation_result(source),
            "pattern_count": counts_by_source.get(str(source.get("repo_rel_path") or ""), 0),
            "timestamp": int(source.get("imported_at") or 0),
            "promotion_method": str(source.get("promotion_method") or "automatic"),
            "promotion_reason": str(source.get("promotion_reason") or derive_pattern_promotion_reason(source)),
        }
        sources.append(entry)
    if filter_state:
        sources = [item for item in sources if item["promotion_state"] == filter_state]
    if filter_tag:
        tag = filter_tag.strip().lower()
        sources = [item for item in sources if tag in {str(entry).strip().lower() for entry in item.get("tags", [])}]
    if search:
        needle = search.strip().lower()
        sources = [item for item in sources if needle in f"{item['path']} {item['source_origin']}".lower()]
    if limit > 0:
        sources = sources[:limit]
    summary = {
        "total_sources": len(sources),
        "curated_trusted": sum(1 for item in sources if item["promotion_state"] == "curated_trusted"),
        "curated_experimental": sum(1 for item in sources if item["promotion_state"] == "curated_experimental"),
        "candidate": sum(1 for item in sources if item["promotion_state"] == "candidate"),
    }
    return {
        "pattern_repo": str(repo_root),
        "summary": summary,
        "sources": sources,
    }


def print_pattern_inspection(summary: dict, records: list[dict], *, show_promotion_state: bool = True) -> None:
    print("Summary:")
    print(f"- total_patterns: {summary.get('total_patterns', 0)}")
    print(f"- curated_trusted: {summary.get('curated_trusted', 0)}")
    print(f"- curated_experimental: {summary.get('curated_experimental', 0)}")
    print(f"- candidate: {summary.get('candidate', 0)}")
    for record in records:
        print(f"\nPattern ID: {record.get('id', '')}")
        if show_promotion_state:
            print(f"- promotion_state: {record.get('promotion_state', '')}")
        print(f"- trust_level: {record.get('trust_level', '')}")
        print(f"- pattern_type: {record.get('pattern_type', '')}")
        print(f"- tags: {record.get('tags', [])}")
        print(f"- applicability_context: {record.get('applicability_context', [])}")
        print(f"- confidence: {record.get('confidence', 0)}")
        print(f"- validation_result: {record.get('validation_result', 'not_recorded')}")
        print(f"- publish_result: {record.get('publish_result', 'not_recorded')}")
        print(f"- regression_status: {record.get('regression_status', 'none')}")
        print(f"- source_file: {record.get('source_file', '')}")
        print(f"- source_origin: {record.get('source_origin', '') or '(none)'}")
        print(f"- last_validated_commit: {record.get('last_validated_commit', '') or '(none)'}")
        print(f"- last_published_commit: {record.get('last_published_commit', '') or '(none)'}")
        print(f"- pr_url: {record.get('pr_url', '') or '(none)'}")
        print(f"- promotion_method: {record.get('promotion_method', 'automatic')}")
        print(f"- promotion_reason: {record.get('promotion_reason', '')}")
        print(f"- timestamp: {record.get('timestamp', 0)}")


def print_pattern_source_inspection(summary: dict, sources: list[dict]) -> None:
    print("Summary:")
    print(f"- total_sources: {summary.get('total_sources', 0)}")
    print(f"- curated_trusted: {summary.get('curated_trusted', 0)}")
    print(f"- curated_experimental: {summary.get('curated_experimental', 0)}")
    print(f"- candidate: {summary.get('candidate', 0)}")
    for source in sources:
        print(
            f"- path={source.get('path', '')} trust={source.get('trust_level', '')} "
            f"promotion_state={source.get('promotion_state', '')} tags={source.get('tags', [])} "
            f"validation_result={source.get('validation_result', 'not_recorded')} "
            f"pattern_count={source.get('pattern_count', 0)} origin={source.get('source_origin', '') or '(none)'}"
        )


def apply_pattern_control_action(current_state: str, action: str) -> str:
    if action == "promote":
        if current_state == "candidate":
            return "curated_experimental"
        if current_state == "curated_experimental":
            return "curated_trusted"
        return current_state
    if action == "demote":
        if current_state == "curated_trusted":
            return "curated_experimental"
        if current_state == "curated_experimental":
            return "candidate"
        return current_state
    return current_state


def trust_for_promotion_state(state: str, current_trust: str = "trusted") -> str:
    if state == "curated_trusted":
        return "trusted"
    if state in {"candidate", "curated_experimental"}:
        return "experimental"
    return current_trust if current_trust in PATTERN_TRUST_LEVELS else "trusted"


def refresh_pattern_memory_from_controls(pattern_repo: Path) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    effective = build_effective_pattern_memory(repo_root)
    save_script_pattern_memory(repo_root, effective)
    return effective


def find_pattern_record(pattern_repo: Path, pattern_id: str) -> dict | None:
    inspection = build_pattern_inspection_records(pattern_repo)
    for pattern in inspection.get("patterns", []):
        if str(pattern.get("id") or "") == pattern_id:
            return pattern
    return None


def find_source_record(pattern_repo: Path, source_ref: str) -> dict | None:
    source_ref = str(source_ref or "").strip()
    if not source_ref:
        return None
    for source in load_effective_pattern_sources(pattern_repo):
        if source_ref in source_control_keys(source):
            return source
    return None


def manage_pattern_state(
    pattern_repo: Path,
    pattern_id: str,
    *,
    action: str,
    set_trust: str = "",
    set_promotion_state: str = "",
    dry_run: bool = False,
) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    pattern = find_pattern_record(repo_root, pattern_id)
    controls = load_pattern_controls(repo_root)
    existing_override = resolve_pattern_override(controls, {"id": pattern_id})
    if not pattern:
        if isinstance(existing_override, dict):
            pattern = {
                "id": pattern_id,
                "promotion_state": str(existing_override.get("promotion_state") or "candidate"),
                "trust_level": str(existing_override.get("trust_level") or "experimental"),
            }
        else:
            return {"ok": False, "reason": "pattern not found", "target_type": "pattern", "target": pattern_id}
    pattern_controls = controls.setdefault("patterns", {})
    previous_state = str(pattern.get("promotion_state") or "")
    previous_trust = str(pattern.get("trust_level") or "")
    new_state = set_promotion_state or apply_pattern_control_action(previous_state, action)
    new_trust = set_trust or trust_for_promotion_state(new_state, previous_trust)
    if action == "forget":
        next_control = {
            "action": "forget",
            "promotion_method": "manual",
            "promotion_reason": "manual forget pattern override",
            "timestamp": int(time.time()),
        }
        affected_pattern_count = 1
    else:
        next_control = {
            "action": action or "set",
            "promotion_state": new_state,
            "trust_level": new_trust,
            "promotion_method": "manual",
            "promotion_reason": f"manual {action or 'set'} pattern override",
            "timestamp": int(time.time()),
        }
        affected_pattern_count = 1
    if not dry_run:
        pattern_controls[pattern_id] = next_control
        save_pattern_controls(repo_root, controls)
        refresh_pattern_memory_from_controls(repo_root)
    return {
        "ok": True,
        "target_type": "pattern",
        "target": pattern_id,
        "previous_state": previous_state,
        "new_state": "forgotten" if action == "forget" else new_state,
        "previous_trust": previous_trust,
        "new_trust": "" if action == "forget" else new_trust,
        "affected_pattern_count": affected_pattern_count,
        "reindexed": not dry_run,
        "dry_run": dry_run,
    }


def manage_source_state(
    pattern_repo: Path,
    source_ref: str,
    *,
    action: str,
    set_trust: str = "",
    set_promotion_state: str = "",
    dry_run: bool = False,
) -> dict:
    repo_root = ensure_pattern_repo(pattern_repo)
    source = find_source_record(repo_root, source_ref)
    if not source:
        return {"ok": False, "reason": "source not found", "target_type": "source", "target": source_ref}
    controls = load_pattern_controls(repo_root)
    source_controls = controls.setdefault("sources", {})
    source_key = str(source.get("id") or source.get("repo_rel_path") or source.get("candidate_path") or source_ref)
    previous_state = str(source.get("effective_promotion_state") or normalize_pattern_promotion_state(source))
    previous_trust = str(source.get("effective_trust_level") or source.get("trust_level") or "trusted")
    new_state = set_promotion_state or apply_pattern_control_action(previous_state, action)
    new_trust = set_trust or trust_for_promotion_state(new_state, previous_trust)
    if action == "forget":
        next_control = {
            "action": "forget",
            "promotion_method": "manual",
            "promotion_reason": "manual forget source override; source file remains on disk",
            "timestamp": int(time.time()),
        }
        new_state_label = "forgotten"
    else:
        next_control = {
            "action": action or "set",
            "promotion_state": new_state,
            "trust_level": new_trust,
            "promotion_method": "manual",
            "promotion_reason": f"manual {action or 'set'} source override",
            "timestamp": int(time.time()),
        }
        new_state_label = new_state
    related_patterns = [
        item
        for item in build_pattern_inspection_records(repo_root).get("patterns", [])
        if str(item.get("source_file") or "") in source_control_keys(source)
        or str(item.get("source_repo_path") or "") in source_control_keys(source)
    ]
    if not dry_run:
        source_controls[source_key] = next_control
        save_pattern_controls(repo_root, controls)
        refresh_pattern_memory_from_controls(repo_root)
    return {
        "ok": True,
        "target_type": "source",
        "target": source_key,
        "previous_state": previous_state,
        "new_state": new_state_label,
        "previous_trust": previous_trust,
        "new_trust": "" if action == "forget" else new_trust,
        "affected_pattern_count": len(related_patterns),
        "reindexed": not dry_run,
        "dry_run": dry_run,
    }


def print_pattern_control_result(result: dict) -> None:
    if not result.get("ok"):
        print("=== PATTERN CONTROL ===")
        print(f"target_type: {result.get('target_type', '(unknown)')}")
        print(f"target: {result.get('target', '(none)')}")
        print(f"error: {result.get('reason', 'unknown error')}")
        return
    print("=== PATTERN CONTROL ===")
    print(f"target_type: {result.get('target_type', '(unknown)')}")
    print(f"target: {result.get('target', '(none)')}")
    print(f"previous_state: {result.get('previous_state', '(none)')}")
    print(f"new_state: {result.get('new_state', '(none)')}")
    print(f"previous_trust: {result.get('previous_trust', '(none)')}")
    print(f"new_trust: {result.get('new_trust', '(none)')}")
    print(f"affected_pattern_count: {result.get('affected_pattern_count', 0)}")
    print(f"reindex_ran: {format_bool(result.get('reindexed'))}")
    print(f"dry_run: {format_bool(result.get('dry_run'))}")


def forget_pattern(pattern_repo: Path, pattern_id: str) -> dict:
    memory = load_script_pattern_memory(pattern_repo)
    retained = [item for item in memory.get("patterns", []) if isinstance(item, dict) and item.get("id") != pattern_id]
    removed = len(retained) != len(memory.get("patterns", []))
    memory["patterns"] = retained
    memory["updated_at"] = int(time.time())
    save_script_pattern_memory(pattern_repo, memory)
    return {"removed": removed, "pattern_id": pattern_id, "remaining_patterns": len(retained)}


def upsert_script_patterns(memory: dict, patterns: list[dict]) -> dict:
    existing = {
        pattern.get("id", ""): pattern
        for pattern in memory.get("patterns", [])
        if isinstance(pattern, dict) and pattern.get("id")
    }
    for pattern in patterns:
        prior = existing.get(pattern["id"], {})
        if isinstance(prior, dict) and prior.get("success_count"):
            pattern["success_count"] = int(prior.get("success_count", 0) or 0)
        existing[pattern["id"]] = pattern
    sources: set[str] = set(memory.get("sources", []))
    for pattern in existing.values():
        for source in pattern.get("source_files", []):
            sources.add(source)
    memory["patterns"] = sorted(existing.values(), key=lambda item: item.get("id", ""))
    memory["sources"] = sorted(sources)
    memory["updated_at"] = int(time.time())
    return memory


def learn_from_scripts(
    repo: Path,
    script_paths: list[str],
    pattern_repo: Path | None = None,
    trust_level: str = "trusted",
    tags: list[str] | None = None,
) -> dict:
    pattern_root = ensure_pattern_repo(pattern_repo or repo)
    memory = load_script_pattern_memory(pattern_root)
    learned_patterns: list[dict] = []
    learned_sources: list[str] = []
    for candidate in script_paths:
        path = Path(candidate)
        if not path.is_absolute():
            path = (repo / path).resolve()
        if not path.exists() or not path.is_file():
            continue
        learned_sources.append(relative_or_name(path, repo))
        source_record = {
            "repo_rel_path": relative_or_name(path, repo),
            "origin_path": str(path),
            "trust_level": trust_level if trust_level in PATTERN_TRUST_LEVELS else "trusted",
            "tags": list(tags or []),
        }
        learned_patterns.extend(extract_script_patterns_with_metadata(repo, path, source_record))
    memory = upsert_script_patterns(memory, learned_patterns)
    save_script_pattern_memory(pattern_root, memory)
    return {
        "pattern_repo": str(pattern_root),
        "learned_sources": sorted(set(learned_sources)),
        "learned_patterns": learned_patterns,
        "memory": memory,
    }


def retrieve_script_patterns(
    memory: dict,
    task_type: str,
    task_text: str,
    script_path: Path | None = None,
    task: dict | None = None,
    include_experimental: bool = False,
    limit: int = 5,
    effectiveness: dict | None = None,
) -> dict:
    query_text = f"{task_type} {task_text}"
    if script_path:
        query_text += f" {script_path.name} {script_path.stem}"
    query_keywords = set(pattern_keywords_from_text(query_text))
    family_candidates: dict[str, dict] = {}
    rejected: list[dict] = []
    forbidden_types = set(task.get("forbidden_pattern_types", [])) if isinstance(task, dict) else set()
    required_types = set(task.get("expected_pattern_types", [])) if isinstance(task, dict) else set()
    for pattern in memory.get("patterns", []):
        if not isinstance(pattern, dict):
            continue
        pattern_type = str(pattern.get("pattern_type", "") or "")
        family = str(pattern.get("family", pattern_type) or pattern_type)
        trust_level = str(pattern.get("trust_level", "trusted") or "trusted")
        score = 0.0
        reasons: list[str] = []
        reject_reasons: list[str] = []
        applicable = task_type in pattern.get("applicability_context", [])
        if task_type in pattern.get("applicability_context", []):
            score += 3.0
            reasons.append(f"context matched {task_type}")
        overlap = sorted(query_keywords & set(pattern.get("keywords", [])))
        if overlap:
            score += min(4.0, float(len(overlap)))
            reasons.append("keyword overlap: " + ", ".join(overlap[:4]))
        if required_types and pattern_type in required_types:
            score += 2.0
            reasons.append("required by task expectations")
        normalized = pattern.get("normalized_examples", {}) if isinstance(pattern.get("normalized_examples"), dict) else {}
        if task_type == "new-script" and pattern.get("pattern_type") in {"cli_style", "entrypoint", "logging_style", "config_loading", "function_organization"}:
            score += 1.5
            reasons.append("useful for new-script scaffolding")
        if task_type == "validation_discovery" and normalized.get("validation_kind"):
            score += 1.5
            reasons.append(f"validation preference: {normalized.get('validation_kind')}")
        if task_type in {"debug", "refactor"} and pattern.get("pattern_type") in {"proxy_handling", "request_session", "retry_backoff", "timeout", "rate_limit_handling", "error_handling"}:
            score += 1.0
            reasons.append("operational pattern relevant to maintenance tasks")
        if task_type == "refactor" and pattern.get("pattern_type") in {"proxy_handling", "request_session", "retry_backoff", "timeout", "rate_limit_handling"}:
            if not ({"retry", "backoff", "rate", "limit", "proxy", "timeout"} & query_keywords):
                score -= 3.5
                reject_reasons.append("rejected because operational pattern was unsupported by refactor prompt")
        if "local" in query_keywords and pattern.get("pattern_type") in {"proxy_handling", "request_session", "retry_backoff", "rate_limit_handling"}:
            score -= 4.5
            reject_reasons.append("rejected for local-only task")
        if trust_level == "trusted":
            score += 1.5
            reasons.append("trusted source")
        else:
            score -= 1.5
            if not include_experimental:
                reject_reasons.append("experimental pattern not applied by default")
            else:
                reasons.append("experimental source allowed")
        confidence = float(pattern.get("confidence", 0) or 0)
        success_count = int(pattern.get("success_count", 0) or 0)
        score += min(2.0, success_count * 0.5)
        family_effectiveness = (effectiveness or {}).get("families", {}).get(family, {})
        success_rate, overapply_rate, avg_delta = effectiveness_stats_view(family_effectiveness, task_type)
        if success_rate:
            score += min(1.5, success_rate * 1.5)
            reasons.append(f"family success_rate={success_rate:.2f}")
        if avg_delta:
            score += max(-0.75, min(0.75, avg_delta / 20.0))
        if overapply_rate:
            score -= min(2.0, overapply_rate * 2.0)
            reject_reasons.append(f"family overapplication penalty={overapply_rate:.2f}") if overapply_rate >= 0.75 else reasons.append(f"family overapplication penalty={overapply_rate:.2f}")
        strict_override = confidence >= 0.92 and len(overlap) >= 2
        if forbidden_types and pattern_type in forbidden_types:
            score -= 6.0
            reject_reasons.append("forbidden for this task")
        if not applicable and not strict_override:
            reject_reasons.append("task-type gating rejected pattern")
        if score <= 0 or reject_reasons:
            rejected.append(
                {
                    "pattern_type": pattern_type,
                    "family": family,
                    "score": round(score + confidence, 2),
                    "reasons": reject_reasons or ["score below threshold"],
                }
            )
            continue
        entry = family_candidates.get(family)
        candidate_score = round(score + confidence, 2)
        if entry is None or candidate_score > entry.get("score", 0):
            family_candidates[family] = {
                "id": pattern.get("id", ""),
                "pattern_type": pattern_type,
                "family": family,
                "summary": pattern.get("summary", ""),
                "source_files": sorted(set(pattern.get("source_files", []))),
                "source_repo_path": pattern.get("source_repo_path", ""),
                "trust_level": trust_level,
                "tags": list(pattern.get("tags", []) or []),
                "confidence": confidence,
                "score": candidate_score,
                "reasons": reasons,
                "normalized_examples": normalized,
                "anti_pattern_note": pattern.get("anti_pattern_note", ""),
                "success_count": success_count,
            }
        else:
            entry["source_files"] = sorted(set(entry.get("source_files", []) + pattern.get("source_files", [])))
            entry["reasons"] = sorted(set(entry.get("reasons", []) + reasons))
            entry["success_count"] = max(entry.get("success_count", 0), success_count)
    considered = sorted(family_candidates.values(), key=lambda item: (item.get("score", 0), item.get("confidence", 0), item.get("success_count", 0)), reverse=True)
    applied: list[dict] = []
    preferred_order = list(task.get("required_applied_patterns", [])) if isinstance(task, dict) else []
    seen_applied: set[str] = set()
    for required_pattern in preferred_order:
        candidate = next((item for item in considered if item.get("pattern_type") == required_pattern), None)
        if not candidate:
            continue
        if candidate.get("score", 0) < 4.5 or candidate.get("confidence", 0) < 0.7:
            continue
        applied.append(candidate)
        seen_applied.add(required_pattern)
        if len(applied) >= 3:
            break
    for item in considered:
        pattern_type = item.get("pattern_type", "")
        if pattern_type in seen_applied:
            continue
        threshold = 4.8 if pattern_type not in required_types else 4.5
        if item.get("score", 0) < threshold or item.get("confidence", 0) < 0.7:
            continue
        applied.append(item)
        seen_applied.add(pattern_type)
        if len(applied) >= 3:
            break
    return {
        "considered": considered[:limit],
        "applied": applied,
        "rejected": sorted(rejected, key=lambda item: item.get("score", 0), reverse=True)[:limit],
        "source_scripts": sorted({source for item in applied for source in item.get("source_files", [])}),
    }


def assess_pattern_repo_coverage(selection: dict, repo_selection: dict) -> dict:
    selected_name = str(repo_selection.get("selected", "none") or "none")
    selected_tags = list(repo_selection.get("tags", []) or [])
    selected_domain_families = domain_pattern_families(selected_name, selected_tags)
    applied_items = [item for item in selection.get("applied", []) if isinstance(item, dict)]
    rejected_items = [item for item in selection.get("rejected", []) if isinstance(item, dict)]
    applied_families = {str(item.get("family", item.get("pattern_type", "")) or item.get("pattern_type", "")) for item in applied_items}
    domain_specific = sorted(
        {
            str(item.get("pattern_type", ""))
            for item in applied_items
            if str(item.get("family", item.get("pattern_type", "")) or item.get("pattern_type", "")) in selected_domain_families
        }
    )
    general = sorted(
        {
            str(item.get("pattern_type", ""))
            for item in applied_items
            if str(item.get("family", item.get("pattern_type", "")) or item.get("pattern_type", "")) not in selected_domain_families
        }
    )
    rejected = sorted({str(item.get("pattern_type", "")) for item in rejected_items})
    is_domain_repo = selected_name not in {"default", "none"} and bool(selected_domain_families)
    domain_coverage_ok = (not is_domain_repo) or bool(domain_specific)
    note = ""
    if is_domain_repo and not domain_coverage_ok:
        note = f"selected repo '{selected_name}' only contributed generic patterns"
    elif is_domain_repo and domain_coverage_ok:
        note = f"selected repo '{selected_name}' contributed domain-specific patterns"
    return {
        "selected_pattern_repo": selected_name,
        "selected_pattern_repo_tags": selected_tags,
        "domain_specific_patterns_applied": domain_specific,
        "general_patterns_applied": general,
        "rejected_patterns": rejected,
        "domain_pattern_families": sorted(selected_domain_families),
        "domain_coverage_ok": domain_coverage_ok,
        "domain_coverage_note": note,
        "is_domain_repo": is_domain_repo,
        "applied_families": sorted(applied_families),
    }


def compare_pattern_baseline(plan: dict, selection: dict) -> dict:
    learned_plan = select_validation_stack(plan, selection)
    baseline_command = plan.get("primary_command", "")
    learned_command = learned_plan.get("primary_command", "")
    patterns_added = [item.get("pattern_type", "") for item in selection.get("applied", []) if item.get("pattern_type")]
    return {
        "baseline_validation_command": baseline_command,
        "learned_validation_command": learned_command,
        "patterns_added": patterns_added,
        "improved_fit": bool(patterns_added) or (learned_command != baseline_command),
    }


def serializable_repo_selection(repo_selection: dict) -> dict:
    serializable = dict(repo_selection)
    if isinstance(serializable.get("path"), Path):
        serializable["path"] = str(serializable["path"])
    return serializable


def refine_repo_confidence(confidence: str, coverage: dict, repo_selection: dict, effectiveness: dict | None, task_type: str) -> str:
    levels = ["low", "medium", "high"]
    current_index = levels.index(confidence) if confidence in levels else 0
    selected_name = str(repo_selection.get("selected", "none") or "none")
    if coverage.get("is_domain_repo") and not coverage.get("domain_coverage_ok"):
        current_index = max(0, current_index - 1)
    repo_effectiveness = ((effectiveness or {}).get("repos", {}) or {}).get(selected_name, {})
    success_rate, overapply_rate, _avg_delta = effectiveness_stats_view(repo_effectiveness, task_type)
    if success_rate >= 0.75 and overapply_rate <= 0.25:
        current_index = min(len(levels) - 1, current_index + 1)
    if overapply_rate >= 0.5:
        current_index = max(0, current_index - 1)
    return levels[current_index]


def resolve_pattern_selection(
    config: dict | None,
    repo_selection: dict,
    task_type: str,
    task_text: str,
    script_path: Path | None = None,
    task: dict | None = None,
    include_experimental: bool = False,
) -> tuple[dict, dict]:
    pattern_repo = repo_selection.get("path")
    if pattern_repo is None:
        empty_selection = retrieve_script_patterns(empty_script_pattern_memory(), task_type, task_text, script_path=script_path, task=task, include_experimental=include_experimental)
        coverage = assess_pattern_repo_coverage(empty_selection, repo_selection)
        empty_selection["coverage"] = coverage
        empty_selection["repo_selection"] = serializable_repo_selection(repo_selection)
        return empty_selection, repo_selection
    repo_path = normalize_pattern_repo_path(pattern_repo)
    effectiveness = load_pattern_effectiveness(repo_path)
    memory = load_script_pattern_memory(repo_path)
    selection = retrieve_script_patterns(
        memory,
        task_type,
        task_text,
        script_path=script_path,
        task=task,
        include_experimental=include_experimental,
        effectiveness=effectiveness,
    )
    coverage = assess_pattern_repo_coverage(selection, repo_selection)
    repo_selection = dict(repo_selection)
    repo_selection["confidence"] = refine_repo_confidence(str(repo_selection.get("confidence", "low") or "low"), coverage, repo_selection, effectiveness, task_type)
    if coverage.get("is_domain_repo") and not coverage.get("domain_coverage_ok"):
        fallback_selection = select_pattern_repo(config, "default", task_type, task_text, script_path=script_path)
        fallback_path = fallback_selection.get("path")
        if fallback_selection.get("selected") == "default" and fallback_path and normalize_pattern_repo_path(fallback_path) != repo_path:
            fallback_memory = load_script_pattern_memory(fallback_path)
            fallback_effectiveness = load_pattern_effectiveness(fallback_path)
            candidate = retrieve_script_patterns(
                fallback_memory,
                task_type,
                task_text,
                script_path=script_path,
                task=task,
                include_experimental=include_experimental,
                effectiveness=fallback_effectiveness,
            )
            candidate_coverage = assess_pattern_repo_coverage(candidate, fallback_selection)
            if candidate.get("applied"):
                candidate["coverage"] = candidate_coverage
                candidate["repo_selection"] = serializable_repo_selection(fallback_selection)
                candidate["fallback_reason"] = "domain repo lacked domain-specific coverage; fell back to default repo"
                fallback_selection["confidence"] = refine_repo_confidence(str(fallback_selection.get("confidence", "low") or "low"), candidate_coverage, fallback_selection, fallback_effectiveness, task_type)
                return candidate, fallback_selection
        empty_repo_selection = {
            "selected": "none",
            "path": None,
            "reason": "domain repo lacked domain-specific coverage and no cleaner fallback was available",
            "confidence": "low",
            "tags": [],
        }
        empty_selection = retrieve_script_patterns(empty_script_pattern_memory(), task_type, task_text, script_path=script_path, task=task, include_experimental=include_experimental)
        empty_selection["coverage"] = assess_pattern_repo_coverage(empty_selection, empty_repo_selection)
        empty_selection["repo_selection"] = serializable_repo_selection(empty_repo_selection)
        empty_selection["fallback_reason"] = "domain repo lacked domain-specific coverage; fell back to no pattern repo"
        return empty_selection, empty_repo_selection
    selection["coverage"] = coverage
    selection["repo_selection"] = serializable_repo_selection(repo_selection)
    return selection, repo_selection


def format_script_pattern_transparency(selection: dict) -> str:
    considered = selection.get("considered", [])
    applied = selection.get("applied", [])
    rejected = selection.get("rejected", [])
    coverage = selection.get("coverage", {}) if isinstance(selection.get("coverage"), dict) else {}
    repo_selection = selection.get("repo_selection", {}) if isinstance(selection.get("repo_selection"), dict) else {}
    lines = [
        "selected pattern repo: " + str(repo_selection.get("selected", "none") or "none"),
        "selected pattern repo confidence: " + str(repo_selection.get("confidence", "low") or "low"),
        "selected pattern repo reason: " + str(repo_selection.get("reason", "") or ""),
        "learned patterns considered: "
        + (str([item.get("pattern_type", "") for item in considered]) if considered else "[]"),
        "learned patterns rejected: "
        + (str([item.get("pattern_type", "") for item in rejected]) if rejected else "[]"),
        "learned patterns applied: "
        + (str([item.get("pattern_type", "") for item in applied]) if applied else "[]"),
        "source scripts: " + (str(selection.get("source_scripts", []) or [])),
        "domain-specific patterns applied: " + str(coverage.get("domain_specific_patterns_applied", []) or []),
        "general patterns applied: " + str(coverage.get("general_patterns_applied", []) or []),
        "rejected patterns: " + str(coverage.get("rejected_patterns", []) or []),
        "domain_coverage_ok: " + format_bool(coverage.get("domain_coverage_ok")),
    ]
    if selection.get("fallback_reason"):
        lines.append("pattern repo fallback: " + str(selection.get("fallback_reason", "")))
    if coverage.get("domain_coverage_note"):
        lines.append("domain coverage note: " + str(coverage.get("domain_coverage_note", "")))
    if rejected:
        why_rejected = [f"{item.get('pattern_type')}: {'; '.join(item.get('reasons', [])[:2])}" for item in rejected]
        lines.append("why they were rejected: " + " | ".join(why_rejected))
    if applied:
        why = [
            f"{item.get('pattern_type')} "
            f"(source={item.get('source_repo_path') or (item.get('source_files') or [''])[0]}, "
            f"trust={item.get('trust_level', 'trusted')}): "
            f"{'; '.join(item.get('reasons', [])[:2])}"
            for item in applied
        ]
        lines.append("why they were chosen: " + " | ".join(why))
    else:
        lines.append("why they were chosen: no relevant learned patterns")
    return "\n".join(lines)


def select_validation_stack(plan: dict, selection: dict) -> dict:
    preferred_kind = ""
    for item in selection.get("applied", []):
        normalized = item.get("normalized_examples", {}) if isinstance(item.get("normalized_examples"), dict) else {}
        if normalized.get("validation_kind"):
            preferred_kind = str(normalized["validation_kind"])
            break
    if not preferred_kind:
        return plan
    chosen = next((step for step in plan.get("chosen_stack", []) if step.get("kind") == preferred_kind), None)
    if not chosen:
        function_step = plan.get("function_validation", {}).get("step")
        if preferred_kind == "function" and isinstance(function_step, dict):
            chosen = function_step
    if not chosen:
        return plan
    copied = dict(plan)
    copied["chosen_stack"] = [plan.get("chosen_stack", [])[0], chosen] if chosen.get("kind") != "syntax" else [chosen]
    copied["primary_command"] = chosen.get("command", copied.get("primary_command", ""))
    return copied


def override_validation_stack_for_new_script(
    plan: dict,
    repo: Path,
    script_path: Path,
    *,
    mode: str = "auto",
    custom_command: str = "",
) -> dict:
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode in {"", "auto"}:
        return dict(plan)
    copied = dict(plan)
    script_rel = str(plan.get("script_rel_path") or relative_script_path(repo, script_path))
    syntax_step = {"command": f"python -m py_compile {shlex.quote(script_rel)}", "kind": "syntax"}
    if normalized_mode == "skip":
        copied["chosen_stack"] = []
        copied["primary_command"] = ""
        copied["limited_validation"] = True
        copied["limited_reason"] = "Validation was skipped by operator request."
        copied["confidence_level"] = "low"
        copied["validation_mode"] = "skip"
        return copied
    if normalized_mode == "syntax":
        copied["chosen_stack"] = [syntax_step]
        copied["primary_command"] = syntax_step["command"]
        copied["limited_validation"] = True
        copied["limited_reason"] = "Validation is limited to syntax checks."
        copied["confidence_level"] = "medium"
        copied["validation_mode"] = "syntax"
        return copied
    if normalized_mode == "cli_help":
        cli_help_step = next((step for step in list(plan.get("chosen_stack") or []) if step.get("kind") == "cli_help"), None)
        if not cli_help_step:
            cli_help_step = {"command": f"python {shlex.quote(script_rel)} --help", "kind": "cli_help"}
        copied["chosen_stack"] = [syntax_step, cli_help_step]
        copied["primary_command"] = cli_help_step["command"]
        copied["limited_validation"] = False
        copied["limited_reason"] = ""
        copied["confidence_level"] = "medium"
        copied["validation_mode"] = "cli_help"
        return copied
    if normalized_mode == "custom" and custom_command.strip():
        custom_step = {"command": custom_command.strip(), "kind": "custom"}
        copied["chosen_stack"] = [syntax_step, custom_step]
        copied["primary_command"] = custom_step["command"]
        copied["limited_validation"] = False
        copied["limited_reason"] = ""
        copied["confidence_level"] = "medium"
        copied["validation_mode"] = "custom"
        return copied
    return copied


def summarize_style_match(style_signals: dict, expected_conventions: list[str]) -> tuple[int, str]:
    if not expected_conventions:
        return 10, "no explicit convention expectations"
    matched = [item for item in expected_conventions if style_signals.get(item)]
    score = int(round((len(matched) / max(1, len(expected_conventions))) * 10))
    summary = "matched conventions: " + (", ".join(matched) if matched else "(none)")
    return score, summary


def analyze_generated_script_style(script_path: Path) -> dict:
    features = extract_script_features(script_path)
    text = features.get("text", "")
    return {
        "argparse": bool(features.get("uses_argparse")),
        "click": bool(features.get("uses_click")),
        "typer": bool(features.get("uses_typer")),
        "main_guard": bool(features.get("has_main_guard")),
        "logging": "logging" in text.lower(),
        "proxy": "proxy" in text.lower(),
        "retry": "retry" in text.lower() or "attempt" in text.lower(),
    }


def extract_generation_urls(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"https?://[^\s'\"<>)]+", str(text or "")):
        candidate = str(match).strip().rstrip(".,")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        found.append(candidate)
    return found


def classify_new_script_network_intent(purpose: str, output_path: Path, explicit_endpoint: str = "") -> dict:
    text = f"{purpose} {output_path.name} {output_path.stem}".strip()
    lowered = text.lower()
    urls = extract_generation_urls(text)
    if explicit_endpoint:
        urls = [explicit_endpoint, *[item for item in urls if item != explicit_endpoint]]
    mentions_api = any(token in lowered for token in ["api", "json", "http", "https", "rest", "graphql", "endpoint"])
    mentions_m3u8 = any(token in lowered for token in ["m3u8", "playlist", "hls", "segment", "variant", "extm3u"])
    mentions_proxy = any(token in lowered for token in ["proxy", "auth", "header", "bearer", "cookie", "redirect"])
    network_detected = bool(urls or mentions_api or mentions_m3u8 or mentions_proxy)
    kind = "local"
    confidence = "low"
    reason = ""
    endpoint = urls[0] if urls else explicit_endpoint
    if endpoint.lower().endswith(".m3u8") or mentions_m3u8:
        kind = "m3u8"
        confidence = "high" if endpoint else "medium"
        reason = "task mentions HLS/M3U8 playlist behavior"
    elif endpoint or mentions_api:
        kind = "api"
        confidence = "high" if endpoint else "medium"
        reason = "task mentions a live API or HTTP response shape"
    elif mentions_proxy:
        kind = "api"
        confidence = "medium"
        reason = "task mentions proxy/auth/header handling for outbound requests"
    return {
        "detected": network_detected,
        "kind": kind,
        "confidence": confidence,
        "reason": reason,
        "endpoint": endpoint,
        "urls": urls,
    }


def summarize_generation_probe_findings(probe_result: dict | None) -> dict:
    if not isinstance(probe_result, dict) or not probe_result:
        return {"used": False, "summary": "", "details": [], "key_findings": []}
    probe_type = str(probe_result.get("probe_type") or "auto")
    details: list[str] = []
    key_findings: list[str] = []
    if probe_type == "m3u8_summary":
        playlist_type = str(probe_result.get("playlist_type") or "unknown")
        variant_count = int(probe_result.get("variant_count") or 0)
        segment_sample_count = int(probe_result.get("segment_sample_count") or 0)
        key_tags_present = bool(probe_result.get("key_tags_present"))
        details.extend(
            [
                f"playlist_type={playlist_type}",
                f"variant_count={variant_count}",
                f"segment_sample_count={segment_sample_count}",
                f"key_tags_present={format_bool(key_tags_present)}",
            ]
        )
        if probe_result.get("relative_uri_mode"):
            details.append(f"uri_mode={probe_result.get('relative_uri_mode')}")
        if probe_result.get("sample_variant_uris"):
            key_findings.append("sample_variants=" + ",".join(list(probe_result.get("sample_variant_uris") or [])[:2]))
        if probe_result.get("sample_segment_uris"):
            key_findings.append("sample_segments=" + ",".join(list(probe_result.get("sample_segment_uris") or [])[:2]))
    else:
        status_code = str(probe_result.get("status_code") or "")
        content_type = str(probe_result.get("content_type") or "")
        details.extend(
            [
                f"status_code={status_code or 'unknown'}",
                f"content_type={content_type or 'unknown'}",
                f"redirected={format_bool(bool(probe_result.get('redirected')))}",
            ]
        )
        if probe_result.get("body_is_json"):
            keys = list(probe_result.get("json_top_level_keys") or [])[:6]
            key_findings.append("json_keys=" + ",".join(keys))
        if probe_result.get("auth_failure_hint"):
            key_findings.append(str(probe_result.get("auth_failure_hint")))
    summary = str(probe_result.get("summary") or probe_result.get("error") or "")
    if key_findings:
        summary = (summary + "; " if summary else "") + "; ".join(key_findings[:3])
    return {
        "used": True,
        "summary": summary,
        "details": details,
        "key_findings": key_findings,
    }


def infer_generation_structure(selection: dict, task_intent: dict, probe_summary: dict) -> dict:
    applied = [item for item in selection.get("applied", []) if isinstance(item, dict)]
    applied_types = {str(item.get("pattern_type") or "") for item in applied}
    collection_patterns = [
        item for item in applied
        if str(item.get("import_scope") or "") in {"repo", "folder"}
        or str(item.get("pattern_type") or "") in {"repo_structure", "shared_helper_structure", "naming_convention"}
    ]
    script_kind = str(task_intent.get("kind") or "local")
    structure = ["main entrypoint", "argument parsing"]
    if script_kind in {"api", "m3u8"}:
        structure.extend(["request helper", "response parsing"])
    if "logging_style" in applied_types:
        structure.append("logging setup")
    if "proxy_handling" in applied_types or task_intent.get("detected"):
        structure.append("proxy-aware networking")
    if "retry_backoff" in applied_types:
        structure.append("retry loop")
    if "shared_helper_structure" in {str(item.get("pattern_type") or "") for item in collection_patterns}:
        structure.append("named helper functions")
    if probe_summary.get("used"):
        structure.append("probe-guided parsing")
    return {
        "script_kind": script_kind,
        "applied_types": sorted(applied_types),
        "collection_patterns": [str(item.get("pattern_type") or "") for item in collection_patterns],
        "structure": structure,
    }


def score_new_script_generation_confidence(
    selection: dict,
    repo_selection: dict,
    task_intent: dict,
    probe_summary: dict,
    validation_plan: dict | None = None,
) -> tuple[str, list[str]]:
    score = 0.0
    reasons: list[str] = []
    applied = [item for item in selection.get("applied", []) if isinstance(item, dict)]
    trusted = [item for item in applied if str(item.get("trust_level") or "trusted") == "trusted"]
    if trusted:
        score += min(3.0, 1.0 + 0.75 * len(trusted))
        reasons.append("trusted learned patterns matched the task")
    elif applied:
        score += 1.25
        reasons.append("some learned patterns matched, but trust or relevance was weaker")
    coverage = selection.get("coverage", {}) if isinstance(selection.get("coverage"), dict) else {}
    if coverage.get("domain_coverage_ok") and coverage.get("domain_specific_patterns_applied"):
        score += 1.5
        reasons.append("domain-specific repo patterns matched the task")
    elif str(repo_selection.get("selected") or "none") not in {"none", "default"} and not coverage.get("domain_coverage_ok", True):
        score -= 1.0
        reasons.append("selected repo had weak domain coverage for this task")
    if probe_summary.get("used") and task_intent.get("detected"):
        score += 2.0
        reasons.append("live endpoint evidence reduced network ambiguity")
    elif task_intent.get("detected") and not probe_summary.get("used"):
        score -= 1.0
        reasons.append("network behavior is inferred without live endpoint evidence")
    if isinstance(validation_plan, dict) and validation_plan.get("primary_command"):
        score += 1.5
        reasons.append("a concrete validation plan was selected")
        if validation_plan.get("limited_validation"):
            score -= 0.5
            reasons.append("validation plan is limited")
    else:
        score -= 1.0
        reasons.append("no strong validation plan was available")
    purpose = str(task_intent.get("purpose") or "")
    if len(purpose.split()) < 3:
        score -= 0.5
        reasons.append("task description is brief and leaves some ambiguity")
    if score >= 5.0:
        return "high", reasons
    if score >= 2.75:
        return "medium", reasons
    return "low", reasons


def build_new_script_generation_plan(
    repo: Path,
    output_path: Path,
    purpose: str,
    selection: dict,
    repo_selection: dict,
    *,
    probe_result: dict | None = None,
) -> dict:
    task_intent = classify_new_script_network_intent(purpose, output_path, explicit_endpoint=str((probe_result or {}).get("endpoint") or ""))
    task_intent["purpose"] = purpose
    probe_summary = summarize_generation_probe_findings(probe_result)
    structure = infer_generation_structure(selection, task_intent, probe_summary)
    selected_patterns = [
        {
            "pattern_type": str(item.get("pattern_type") or ""),
            "trust_level": str(item.get("trust_level") or "trusted"),
            "source_repo_path": str(item.get("source_repo_path") or ""),
            "reason": "; ".join(list(item.get("reasons") or [])[:2]),
        }
        for item in selection.get("applied", [])
        if isinstance(item, dict)
    ]
    validation_outline = (
        "CLI help or import-based validation will be selected after rendering."
        if structure.get("script_kind") == "local"
        else "Generated networking code will be validated with syntax plus the safest available runtime or import check."
    )
    return {
        "task_purpose": purpose,
        "output_path": str(output_path),
        "pattern_source_used": str(repo_selection.get("selected") or "none"),
        "pattern_repo_reason": str(repo_selection.get("reason") or ""),
        "task_intent": task_intent,
        "selected_patterns": selected_patterns,
        "probe_used": bool(probe_summary.get("used")),
        "probe_summary": probe_summary,
        "proposed_script_structure": structure.get("structure", []),
        "script_kind": structure.get("script_kind", "local"),
        "validation_outline": validation_outline,
        "generation_confidence": "low",
        "confidence_reasons": [],
    }


def finalize_new_script_generation_plan(plan: dict, validation_plan: dict, selection: dict | None = None, repo_selection: dict | None = None) -> dict:
    finalized = dict(plan)
    validation_summary = ""
    if validation_plan.get("primary_command"):
        validation_summary = str(validation_plan.get("primary_command") or "")
    elif validation_plan.get("chosen_stack"):
        validation_summary = ", ".join(
            str(item.get("command") or "") for item in list(validation_plan.get("chosen_stack") or [])[:2] if item.get("command")
        )
    finalized["validation_plan"] = validation_summary or "no strong validation plan"
    confidence, reasons = score_new_script_generation_confidence(
        selection or {
            "applied": list(finalized.get("selected_patterns") or []),
            "coverage": {},
        },
        repo_selection or {"selected": finalized.get("pattern_source_used", "none")},
        finalized.get("task_intent", {}),
        finalized.get("probe_summary", {}),
        validation_plan,
    )
    finalized["generation_confidence"] = confidence
    finalized["confidence_reasons"] = reasons
    return finalized


def maybe_prepare_new_script_probe(
    purpose: str,
    output_path: Path,
    *,
    explicit_probe_result: dict | None = None,
    explicit_probe_url: str = "",
    explicit_probe_type: str = "auto",
    probe_headers: dict[str, str] | None = None,
    bearer_token: str = "",
    cookies: str = "",
    user_agent: str = "",
    method: str = "",
    http_proxy: str = "",
    https_proxy: str = "",
    timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_PROBE_MAX_BYTES,
    follow_up_limit: int = DEFAULT_PROBE_FOLLOW_UP_LIMIT,
) -> dict:
    if isinstance(explicit_probe_result, dict) and explicit_probe_result:
        return explicit_probe_result
    task_intent = classify_new_script_network_intent(purpose, output_path, explicit_endpoint=explicit_probe_url)
    endpoint = str(explicit_probe_url or task_intent.get("endpoint") or "").strip()
    if not task_intent.get("detected") or task_intent.get("confidence") != "high" or not endpoint:
        return {}
    probe_type = explicit_probe_type if explicit_probe_type and explicit_probe_type != "auto" else (
        "m3u8_summary" if str(task_intent.get("kind") or "") == "m3u8" else "json_summary"
    )
    return probe_endpoint(
        endpoint,
        probe_type=probe_type,
        method=method,
        custom_headers=probe_headers or {},
        bearer_token=bearer_token,
        cookies=cookies,
        user_agent=user_agent,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        timeout_seconds=max(1, int(timeout_seconds or DEFAULT_PROBE_TIMEOUT_SECONDS)),
        max_bytes=max(512, int(max_bytes or DEFAULT_PROBE_MAX_BYTES)),
        follow_up_limit=max(0, int(follow_up_limit or DEFAULT_PROBE_FOLLOW_UP_LIMIT)),
    )


def render_new_script(repo: Path, output_path: Path, purpose: str, selection: dict, generation_plan: dict | None = None) -> dict:
    applied_types = {item.get("pattern_type", "") for item in selection.get("applied", [])}
    generation_plan = generation_plan or {}
    script_kind = str(generation_plan.get("script_kind") or "local")
    probe_result = generation_plan.get("probe_result") if isinstance(generation_plan.get("probe_result"), dict) else {}
    cli_style = "argparse"
    for item in selection.get("applied", []):
        normalized = item.get("normalized_examples", {}) if isinstance(item.get("normalized_examples"), dict) else {}
        if item.get("pattern_type") == "cli_style" and normalized.get("style") in {"argparse", "manual"}:
            cli_style = normalized["style"]
            break
    use_logging = "logging_style" in applied_types
    use_proxy = "proxy_handling" in applied_types or script_kind in {"api", "m3u8"}
    use_retry = "retry_backoff" in applied_types
    entrypoint = "main"
    if any(item.get("pattern_type") == "entrypoint" for item in selection.get("applied", [])):
        entrypoint = "main"
    docstring = purpose.strip() or f"Local utility generated for {output_path.stem}."
    default_url = ""
    if probe_result:
        candidate = str(probe_result.get("final_url") or probe_result.get("endpoint") or "").strip()
        safe_candidate, redacted = redact_probe_url(candidate)
        default_url = "" if redacted else safe_candidate
    elif generation_plan.get("task_intent", {}).get("endpoint"):
        candidate = str(generation_plan.get("task_intent", {}).get("endpoint") or "")
        safe_candidate, redacted = redact_probe_url(candidate)
        default_url = "" if redacted else safe_candidate
    key_names = list((probe_result or {}).get("json_top_level_keys") or [])[:6]
    playlist_type = str((probe_result or {}).get("playlist_type") or "unknown")
    variant_count = int((probe_result or {}).get("variant_count") or 0)
    key_tags_present = bool((probe_result or {}).get("key_tags_present"))
    target_duration = int((probe_result or {}).get("target_duration") or 0)
    media_sequence = int((probe_result or {}).get("media_sequence") or 0)

    if script_kind == "api":
        lines = [f'""" {docstring} """'.replace('" ', '"').replace(' "', '"')]
        imports = [
            "import argparse",
            "import json",
            "import os",
            "import urllib.error",
            "import urllib.request",
        ]
        if use_logging:
            imports.append("import logging")
        lines.extend(imports)
        lines.append("")
        if default_url:
            lines.append(f"DEFAULT_URL = {default_url!r}")
        else:
            lines.append("DEFAULT_URL = ''")
        lines.append(f"PROBED_TOP_LEVEL_KEYS = {key_names!r}")
        lines.append("")
        lines.append("def configured_proxy_map() -> dict[str, str]:")
        lines.append("    proxies: dict[str, str] = {}")
        lines.append("    for key, scheme in [('HTTP_PROXY', 'http'), ('HTTPS_PROXY', 'https')]:")
        lines.append("        value = os.getenv(key) or os.getenv(key.lower())")
        lines.append("        if value:")
        lines.append("            proxies[scheme] = value")
        lines.append("    return proxies")
        lines.append("")
        lines.append("def build_headers(args: object) -> dict[str, str]:")
        lines.append("    headers: dict[str, str] = {'Accept': 'application/json'}")
        lines.append("    user_agent = getattr(args, 'user_agent', '')")
        lines.append("    bearer_token = getattr(args, 'bearer_token', '')")
        lines.append("    header_items = list(getattr(args, 'header', []) or [])")
        lines.append("    if user_agent:")
        lines.append("        headers['User-Agent'] = user_agent")
        lines.append("    if bearer_token:")
        lines.append("        headers['Authorization'] = f'Bearer {bearer_token}'")
        lines.append("    for item in header_items:")
        lines.append("        if ':' not in item:")
        lines.append("            continue")
        lines.append("        name, value = item.split(':', 1)")
        lines.append("        headers[name.strip()] = value.strip()")
        lines.append("    return headers")
        lines.append("")
        lines.append("def fetch_json(url: str, timeout: float, headers: dict[str, str], proxies: dict[str, str]) -> dict:")
        lines.append("    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))")
        lines.append("    request = urllib.request.Request(url, headers=headers)")
        lines.append("    with opener.open(request, timeout=timeout) as response:")
        lines.append("        payload = response.read()")
        lines.append("        final_url = response.geturl()")
        lines.append("        status_code = getattr(response, 'status', response.getcode())")
        lines.append("        content_type = response.headers.get_content_type()")
        lines.append("        parsed = json.loads(payload.decode('utf-8', errors='replace'))")
        lines.append("    return {")
        lines.append("        'status_code': status_code,")
        lines.append("        'final_url': final_url,")
        lines.append("        'content_type': content_type,")
        lines.append("        'top_level_keys': list(parsed.keys())[:10] if isinstance(parsed, dict) else [],")
        lines.append("        'payload': parsed,")
        lines.append("    }")
        lines.append("")
        lines.append(f"def {entrypoint}() -> int:")
        lines.append("    parser = argparse.ArgumentParser()")
        lines.append("    parser.add_argument('--url', default=DEFAULT_URL)")
        lines.append("    parser.add_argument('--timeout', type=float, default=10.0)")
        lines.append("    parser.add_argument('--user-agent', default='')")
        lines.append("    parser.add_argument('--bearer-token', default='')")
        lines.append("    parser.add_argument('--header', action='append', default=[])")
        lines.append("    args = parser.parse_args()")
        lines.append("    if not args.url:")
        lines.append("        parser.error('Provide --url or generate the script with a concrete endpoint.')")
        if use_logging:
            lines.append("    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')")
            lines.append("    logging.info('requesting %s', args.url)")
        lines.append("    result = fetch_json(args.url, args.timeout, build_headers(args), configured_proxy_map())")
        lines.append("    summary = {")
        lines.append("        'status_code': result['status_code'],")
        lines.append("        'final_url': result['final_url'],")
        lines.append("        'content_type': result['content_type'],")
        lines.append("        'top_level_keys': result['top_level_keys'],")
        lines.append("        'probed_top_level_keys': PROBED_TOP_LEVEL_KEYS,")
        lines.append("    }")
        lines.append("    print(json.dumps(summary, indent=2, sort_keys=True))")
        lines.append("    return 0")
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append(f"    raise SystemExit({entrypoint}())")
        content = "\n".join(lines) + "\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        return {
            "path": str(output_path),
            "content": content,
            "selection": selection,
            "script_kind": script_kind,
        }

    if script_kind == "m3u8":
        lines = [f'""" {docstring} """'.replace('" ', '"').replace(' "', '"')]
        imports = [
            "import argparse",
            "import json",
            "import os",
            "import urllib.request",
            "from urllib.parse import urljoin",
        ]
        if use_logging:
            imports.append("import logging")
        lines.extend(imports)
        lines.append("")
        lines.append(f"DEFAULT_URL = {default_url!r}" if default_url else "DEFAULT_URL = ''")
        lines.append(f"PROBED_PLAYLIST_TYPE = {playlist_type!r}")
        lines.append(f"PROBED_VARIANT_COUNT = {variant_count}")
        lines.append(f"PROBED_KEY_TAGS_PRESENT = {key_tags_present!r}")
        lines.append(f"PROBED_TARGET_DURATION = {target_duration}")
        lines.append(f"PROBED_MEDIA_SEQUENCE = {media_sequence}")
        lines.append("")
        lines.append("def configured_proxy_map() -> dict[str, str]:")
        lines.append("    proxies: dict[str, str] = {}")
        lines.append("    for key, scheme in [('HTTP_PROXY', 'http'), ('HTTPS_PROXY', 'https')]:")
        lines.append("        value = os.getenv(key) or os.getenv(key.lower())")
        lines.append("        if value:")
        lines.append("            proxies[scheme] = value")
        lines.append("    return proxies")
        lines.append("")
        lines.append("def fetch_text(url: str, timeout: float) -> str:")
        lines.append("    opener = urllib.request.build_opener(urllib.request.ProxyHandler(configured_proxy_map()))")
        lines.append("    with opener.open(url, timeout=timeout) as response:")
        lines.append("        return response.read().decode('utf-8', errors='replace')")
        lines.append("")
        lines.append("def summarize_playlist(base_url: str, text: str) -> dict[str, object]:")
        lines.append("    lines = [line.strip() for line in text.splitlines() if line.strip()]")
        lines.append("    variant_uris = [lines[index + 1] for index, line in enumerate(lines[:-1]) if line.startswith('#EXT-X-STREAM-INF')]")
        lines.append("    segment_uris = [line for line in lines if not line.startswith('#')]")
        lines.append("    key_tags = [line for line in lines if line.startswith('#EXT-X-KEY')]")
        lines.append("    playlist_type = 'master' if variant_uris else 'media'")
        lines.append("    target_duration = next((int(line.split(':', 1)[1]) for line in lines if line.startswith('#EXT-X-TARGETDURATION:')), 0)")
        lines.append("    media_sequence = next((int(line.split(':', 1)[1]) for line in lines if line.startswith('#EXT-X-MEDIA-SEQUENCE:')), 0)")
        lines.append("    sample_variants = [urljoin(base_url, item) for item in variant_uris[:3]]")
        lines.append("    sample_segments = [urljoin(base_url, item) for item in segment_uris[:3]]")
        lines.append("    uri_mode = 'relative' if any(not item.startswith(('http://', 'https://')) for item in variant_uris[:1] + segment_uris[:1]) else 'absolute'")
        lines.append("    return {")
        lines.append("        'playlist_type': playlist_type,")
        lines.append("        'variant_count': len(variant_uris),")
        lines.append("        'segment_count': len(segment_uris),")
        lines.append("        'sample_variant_uris': sample_variants,")
        lines.append("        'sample_segment_uris': sample_segments,")
        lines.append("        'key_tags_present': bool(key_tags),")
        lines.append("        'target_duration': target_duration,")
        lines.append("        'media_sequence': media_sequence,")
        lines.append("        'uri_mode': uri_mode,")
        lines.append("        'probed_playlist_type': PROBED_PLAYLIST_TYPE,")
        lines.append("        'probed_variant_count': PROBED_VARIANT_COUNT,")
        lines.append("        'probed_key_tags_present': PROBED_KEY_TAGS_PRESENT,")
        lines.append("    }")
        lines.append("")
        lines.append(f"def {entrypoint}() -> int:")
        lines.append("    parser = argparse.ArgumentParser()")
        lines.append("    parser.add_argument('--url', default=DEFAULT_URL)")
        lines.append("    parser.add_argument('--timeout', type=float, default=10.0)")
        lines.append("    args = parser.parse_args()")
        lines.append("    if not args.url:")
        lines.append("        parser.error('Provide --url or generate the script with a concrete playlist endpoint.')")
        if use_logging:
            lines.append("    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')")
            lines.append("    logging.info('fetching playlist %s', args.url)")
        lines.append("    summary = summarize_playlist(args.url, fetch_text(args.url, args.timeout))")
        lines.append("    print(json.dumps(summary, indent=2, sort_keys=True))")
        lines.append("    return 0")
        lines.append("")
        lines.append('if __name__ == "__main__":')
        lines.append(f"    raise SystemExit({entrypoint}())")
        content = "\n".join(lines) + "\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        return {
            "path": str(output_path),
            "content": content,
            "selection": selection,
            "script_kind": script_kind,
        }

    lines = [f'""" {docstring} """'.replace('" ', '"').replace(' "', '"')]
    imports = ["import argparse"]
    if "config_loading" in applied_types:
        imports.extend(["import json", "from pathlib import Path"])
    if use_proxy:
        imports.append("import os")
    if use_logging:
        imports.append("import logging")
    lines.extend(imports)
    lines.append("")
    if "config_loading" in applied_types:
        lines.append("def load_aliases(path: str) -> dict[str, str]:")
        lines.append("    config_path = Path(path)")
        lines.append("    if not config_path.exists():")
        lines.append("        return {}")
        lines.append("    return json.loads(config_path.read_text())")
        lines.append("")
    if use_proxy:
        lines.append("def configured_proxy() -> str:")
        lines.append("    return os.getenv('HTTP_PROXY') or os.getenv('http_proxy') or ''")
        lines.append("")
    lines.append("def normalize_name(value: str) -> str:")
    lines.append('    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")')
    lines.append('    return "-".join(part for part in normalized.split("-") if part)')
    lines.append("")
    lines.append(f"def {entrypoint}() -> int:")
    if cli_style == "argparse":
        lines.append("    parser = argparse.ArgumentParser()")
        lines.append('    parser.add_argument("value")')
        lines.append('    parser.add_argument("--prefix", default="")')
        if "config_loading" in applied_types:
            lines.append('    parser.add_argument("--config", default="")')
        lines.append("    args = parser.parse_args()")
    else:
        lines.append("    import sys")
        lines.append("    args = argparse.Namespace(value=sys.argv[1] if len(sys.argv) > 1 else '', prefix='')")
    if use_logging:
        lines.append("    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')")
        lines.append("    logging.info('normalizing value')")
    if "config_loading" in applied_types:
        lines.append("    aliases = load_aliases(args.config) if args.config else {}")
        lines.append("    value = aliases.get(args.value, args.value)")
    else:
        lines.append("    value = args.value")
    lines.append("    normalized = normalize_name(args.value)")
    if "config_loading" in applied_types:
        lines.append("    normalized = normalize_name(value)")
    lines.append("    if args.prefix:")
    lines.append("        normalized = normalize_name(args.prefix) + '-' + normalized")
    if use_proxy:
        lines.append("    proxy_value = configured_proxy()")
        lines.append("    if proxy_value:")
        lines.append("        print(f'proxy={proxy_value}')")
    if use_retry:
        lines.append("    # Retry behavior can be added around external operations if this utility grows beyond local normalization.")
    lines.append("    print(normalized)")
    lines.append("    return 0")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append(f"    raise SystemExit({entrypoint}())")
    content = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    return {
        "path": str(output_path),
        "content": content,
        "selection": selection,
        "script_kind": script_kind,
    }


def prepare_new_script_generation(
    repo: Path,
    output_path: Path,
    purpose: str,
    selection: dict,
    repo_selection: dict,
    *,
    probe_result: dict | None = None,
    probe_url: str = "",
    probe_type: str = "auto",
    probe_headers: dict[str, str] | None = None,
    probe_bearer_token: str = "",
    probe_cookie: str = "",
    probe_user_agent: str = "",
    probe_method: str = "",
    http_proxy: str = "",
    https_proxy: str = "",
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    probe_max_bytes: int = DEFAULT_PROBE_MAX_BYTES,
    probe_follow_up: int = DEFAULT_PROBE_FOLLOW_UP_LIMIT,
) -> dict:
    resolved_probe = maybe_prepare_new_script_probe(
        purpose,
        output_path,
        explicit_probe_result=probe_result,
        explicit_probe_url=probe_url,
        explicit_probe_type=probe_type,
        probe_headers=probe_headers,
        bearer_token=probe_bearer_token,
        cookies=probe_cookie,
        user_agent=probe_user_agent,
        method=probe_method,
        http_proxy=http_proxy,
        https_proxy=https_proxy,
        timeout_seconds=probe_timeout,
        max_bytes=probe_max_bytes,
        follow_up_limit=probe_follow_up,
    )
    generation_plan = build_new_script_generation_plan(
        repo,
        output_path,
        purpose,
        selection,
        repo_selection,
        probe_result=resolved_probe,
    )
    generation_plan["probe_result"] = resolved_probe
    rendered = render_new_script(repo, output_path, purpose, selection, generation_plan)
    validation_plan = build_script_validation_plan(output_path.parent, output_path)
    if resolved_probe:
        validation_plan = apply_probe_findings_to_validation_plan(validation_plan, [resolved_probe])
    chosen_plan = select_validation_stack(validation_plan, selection)
    finalized_generation_plan = finalize_new_script_generation_plan(generation_plan, chosen_plan, selection, repo_selection)
    return {
        "rendered": rendered,
        "generation_plan": finalized_generation_plan,
        "validation_plan": validation_plan,
        "chosen_validation_plan": chosen_plan,
        "probe_result": resolved_probe,
    }


def load_pattern_eval_tasks(repo: Path, tasks_path: str | None) -> tuple[list[dict], Path]:
    default_path = repo / "evals" / "pattern_learning" / "tasks.json"
    path = Path(tasks_path).expanduser().resolve() if tasks_path else default_path.resolve()
    if not path.exists():
        return [], path
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return [], path
    tasks = loaded.get("tasks", []) if isinstance(loaded, dict) else []
    return [task for task in tasks if isinstance(task, dict)], path


def apply_eval_setup_files(root: Path, task: dict) -> None:
    setup_files = task.get("correctness_setup_files", {})
    if not isinstance(setup_files, dict):
        return
    for rel_path, content in setup_files.items():
        target = root / str(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content))


def evaluate_pattern_eval_correctness(
    root: Path,
    task: dict,
    record: dict,
    chosen_kind: str,
) -> tuple[bool, str]:
    if not record.get("validation_success"):
        return False, "validation failed"
    required_kind = str(task.get("required_validation_kind", "") or "")
    if required_kind and chosen_kind != required_kind:
        return False, f"required validation kind {required_kind}, got {chosen_kind or 'none'}"
    required_patterns = set(task.get("required_applied_patterns", []))
    applied_patterns = set(record.get("patterns_applied", []))
    if required_patterns and not required_patterns.issubset(applied_patterns):
        missing = sorted(required_patterns - applied_patterns)
        return False, "missing required patterns: " + ", ".join(missing)
    forbidden_patterns = set(task.get("forbidden_applied_patterns", []))
    if forbidden_patterns & applied_patterns:
        return False, "forbidden patterns applied: " + ", ".join(sorted(forbidden_patterns & applied_patterns))
    command = str(task.get("correctness_command", "") or "")
    if not command:
        return True, "no extra correctness check"
    apply_eval_setup_files(root, task)
    formatted = command.format(script=task.get("output_name", task.get("script", "")))
    code, output = run_subprocess(formatted, root, shell=True)
    if code != int(task.get("correctness_expected_exit", 0) or 0):
        return False, output.strip() or f"correctness command exited {code}"
    expected_contains = task.get("correctness_expected_contains", [])
    if isinstance(expected_contains, str):
        expected_contains = [expected_contains]
    for token in expected_contains:
        if str(token) not in output:
            return False, f"correctness output missing token: {token}"
    return True, "correctness check passed"


def score_pattern_eval_task(task: dict, record: dict) -> dict:
    expected = set(task.get("expected_pattern_types", []))
    forbidden = set(task.get("forbidden_pattern_types", []))
    applied = set(record.get("patterns_applied", []))
    considered = set(record.get("patterns_considered", []))
    correctness = 50 if record.get("correctness_pass") else 0
    relevance = 20 if not expected else int(round(20 * len(expected & considered) / max(1, len(expected))))
    matched_task = 15 if not expected else int(round(15 * len(expected & applied) / max(1, len(expected))))
    over_application_count = len(forbidden & applied)
    avoided_irrelevant = 15 if not over_application_count else max(-20, 15 - 18 * over_application_count)
    style_score = int(record.get("style_match_score", 0) or 0)
    total = correctness + relevance + matched_task + avoided_irrelevant + style_score
    return {
        "correctness": correctness,
        "correctness_pass": bool(record.get("correctness_pass")),
        "relevance": relevance,
        "matched_task_type": matched_task,
        "avoided_irrelevant": avoided_irrelevant,
        "style_score": style_score,
        "over_application_penalty": min(0, avoided_irrelevant),
        "total": total,
    }


def run_pattern_eval_mode(
    repo: Path,
    task: dict,
    memory: dict,
    use_learned_patterns: bool,
    pattern_repo_selection: dict | None = None,
    pattern_effectiveness: dict | None = None,
    config: dict | None = None,
) -> dict:
    task_type = str(task.get("task_type", "") or "debug")
    task_prompt = str(task.get("prompt", "") or "")
    eval_root = repo / "evals" / "pattern_learning"
    script_path = eval_root / str(task.get("script", "")).strip() if task.get("script") else None
    if use_learned_patterns:
        selected = pattern_repo_selection or {"selected": "default", "path": repo, "reason": "eval default", "confidence": "medium", "tags": []}
        selection, selected = resolve_pattern_selection(config or {}, selected, task_type, task_prompt, script_path=script_path, task=task)
    else:
        selection = retrieve_script_patterns(empty_script_pattern_memory(), task_type, task_prompt, script_path=script_path, task=task)
        empty_repo_selection = {"selected": "none", "path": None, "reason": "baseline disabled learned patterns", "confidence": "high", "tags": []}
        selection["coverage"] = assess_pattern_repo_coverage(selection, empty_repo_selection)
        selection["repo_selection"] = empty_repo_selection
    validation_command = ""
    validation_success = False
    validation_output = ""
    files_changed: list[str] = []
    style_match_score = 0
    style_summary = "no style summary"
    chosen_validation_kind = ""
    correctness_pass = False
    correctness_reason = ""

    if task_type == "new-script":
        workspace = Path(tempfile.mkdtemp(prefix="lfa-pattern-eval-", dir=str(repo)))
        output_name = str(task.get("output_name", "generated_tool.py"))
        output_path = workspace / output_name
        render_new_script(workspace, output_path, task_prompt, selection)
        plan = build_script_validation_plan(workspace, output_path)
        chosen_plan = select_validation_stack(plan, selection) if use_learned_patterns else plan
        validation_command = chosen_plan.get("primary_command", "")
        chosen_validation_kind = next((step.get("kind", "") for step in chosen_plan.get("chosen_stack", []) if step.get("kind") != "syntax"), "syntax")
        validation_result = run_validation_stack(workspace, chosen_plan)
        validation_success = bool(validation_result.get("ok"))
        validation_output = validation_result.get("output", "")
        files_changed = [str(Path(output_name))]
        style_signals = analyze_generated_script_style(output_path)
        style_match_score, style_summary = summarize_style_match(style_signals, task.get("expected_conventions", []))
        correctness_pass, correctness_reason = evaluate_pattern_eval_correctness(workspace, task, {
            "validation_success": validation_success,
            "patterns_applied": [item.get("pattern_type", "") for item in selection.get("applied", [])],
        }, chosen_validation_kind)
    else:
        if script_path is None:
            return {
                "task_id": task.get("id", ""),
                "task_type": task_type,
                "patterns_considered": [],
                "patterns_applied": [],
                "validation_command": "",
                "validation_success": False,
                "validation_output": "missing eval script",
                "files_changed": [],
                "final_outcome": "failed",
                "style_match_score": 0,
                "style_match_summary": "missing eval script",
                "selection": selection,
            }
        plan = build_script_validation_plan(eval_root, script_path)
        chosen_plan = select_validation_stack(plan, selection) if use_learned_patterns else plan
        validation_command = chosen_plan.get("primary_command", "")
        chosen_validation_kind = next((step.get("kind", "") for step in chosen_plan.get("chosen_stack", []) if step.get("kind") != "syntax"), "syntax")
        validation_result = run_validation_stack(eval_root, chosen_plan)
        validation_success = bool(validation_result.get("ok"))
        validation_output = validation_result.get("output", "")
        style_signals = analyze_generated_script_style(script_path)
        style_match_score, style_summary = summarize_style_match(style_signals, task.get("expected_conventions", []))
        correctness_pass, correctness_reason = evaluate_pattern_eval_correctness(eval_root, task, {
            "validation_success": validation_success,
            "patterns_applied": [item.get("pattern_type", "") for item in selection.get("applied", [])],
        }, chosen_validation_kind)

    record = {
        "task_id": task.get("id", ""),
        "task_type": task_type,
        "patterns_considered": [item.get("pattern_type", "") for item in selection.get("considered", [])],
        "patterns_rejected": [item.get("pattern_type", "") for item in selection.get("rejected", [])],
        "patterns_applied": [item.get("pattern_type", "") for item in selection.get("applied", [])],
        "validation_command": validation_command,
        "validation_kind": chosen_validation_kind,
        "validation_success": validation_success,
        "validation_output": validation_output,
        "files_changed": files_changed,
        "correctness_pass": correctness_pass,
        "correctness_reason": correctness_reason,
        "final_outcome": "success" if correctness_pass else "failed",
        "style_match_score": style_match_score,
        "style_match_summary": style_summary,
        "selection": selection,
    }
    record["score"] = score_pattern_eval_task(task, record)
    return record


def summarize_pattern_eval_comparison(baseline_runs: list[dict], learned_runs: list[dict]) -> dict:
    def pass_rate(items: list[dict]) -> float:
        return sum(1 for item in items if item.get("validation_success")) / len(items) if items else 0.0

    def correctness_rate(items: list[dict]) -> float:
        return sum(1 for item in items if item.get("correctness_pass")) / len(items) if items else 0.0

    def avg_score(items: list[dict]) -> float:
        values = [float((item.get("score") or {}).get("total", 0)) for item in items]
        return (sum(values) / len(values)) if values else 0.0

    improved: list[str] = []
    regressed: list[str] = []
    over_applied: list[str] = []
    changed_outcome: list[str] = []
    baseline_by_id = {item.get("task_id", ""): item for item in baseline_runs}
    for learned in learned_runs:
        task_id = learned.get("task_id", "")
        base = baseline_by_id.get(task_id, {})
        if bool(learned.get("correctness_pass")) != bool(base.get("correctness_pass")):
            changed_outcome.append(task_id)
        if float((learned.get("score") or {}).get("total", 0)) > float((base.get("score") or {}).get("total", 0)):
            improved.append(task_id)
        elif float((learned.get("score") or {}).get("total", 0)) < float((base.get("score") or {}).get("total", 0)):
            regressed.append(task_id)
        if (not learned.get("correctness_pass") and learned.get("patterns_applied")) or (
            set(learned.get("patterns_applied", [])) & set((baseline_by_id.get(task_id, {}) or {}).get("patterns_rejected", []))
        ):
            over_applied.append(task_id)
    return {
        "baseline_pass_rate": round(pass_rate(baseline_runs), 2),
        "learned_pass_rate": round(pass_rate(learned_runs), 2),
        "baseline_correctness_rate": round(correctness_rate(baseline_runs), 2),
        "learned_correctness_rate": round(correctness_rate(learned_runs), 2),
        "baseline_average_score": round(avg_score(baseline_runs), 2),
        "learned_average_score": round(avg_score(learned_runs), 2),
        "tasks_improved": improved,
        "tasks_regressed": regressed,
        "over_application_cases": over_applied,
        "tasks_changed_outcome": changed_outcome,
    }


def record_pattern_effectiveness(pattern_repo: Path | None, learned_runs: list[dict], baseline_runs: list[dict] | None = None) -> None:
    if pattern_repo is None:
        return
    repo_root = ensure_pattern_repo(pattern_repo)
    effectiveness = load_pattern_effectiveness(repo_root)
    baseline_by_id = {item.get("task_id", ""): item for item in baseline_runs or []}
    for run in learned_runs:
        task_type = str(run.get("task_type", "") or "debug")
        score_delta = float((run.get("score") or {}).get("total", 0) or 0) - float(((baseline_by_id.get(run.get("task_id", ""), {}) or {}).get("score") or {}).get("total", 0) or 0)
        selected_repo = ((run.get("selection") or {}).get("repo_selection", {}) or {}).get("selected", "none")
        coverage = ((run.get("selection") or {}).get("coverage", {}) or {})
        repo_record = effectiveness.setdefault("repos", {}).setdefault(str(selected_repo), {})
        update_effectiveness_stats(
            repo_record,
            task_type,
            considered=True,
            applied=bool(run.get("patterns_applied")),
            successful=bool(run.get("correctness_pass")),
            overapplied=bool(not run.get("correctness_pass") and run.get("patterns_applied")) or bool((coverage.get("is_domain_repo") and not coverage.get("domain_coverage_ok"))),
            score_delta=score_delta,
        )
        if coverage.get("is_domain_repo") and not coverage.get("domain_coverage_ok"):
            repo_record["times_domain_coverage_failed"] = int(repo_record.get("times_domain_coverage_failed", 0) or 0) + 1
        for family in run.get("patterns_considered", []):
            family_record = effectiveness.setdefault("families", {}).setdefault(str(family), {})
            update_effectiveness_stats(family_record, task_type, considered=True)
        applied_families = set((run.get("selection") or {}).get("coverage", {}).get("applied_families", []) or run.get("patterns_applied", []))
        overapplied_families = set(run.get("patterns_applied", [])) if not run.get("correctness_pass") else set()
        for family in applied_families:
            family_record = effectiveness.setdefault("families", {}).setdefault(str(family), {})
            update_effectiveness_stats(
                family_record,
                task_type,
                applied=True,
                successful=bool(run.get("correctness_pass")),
                overapplied=family in overapplied_families,
                score_delta=score_delta,
            )
        task_record = effectiveness.setdefault("task_types", {}).setdefault(task_type, {})
        update_effectiveness_stats(
            task_record,
            task_type,
            considered=True,
            applied=bool(run.get("patterns_applied")),
            successful=bool(run.get("correctness_pass")),
            overapplied=bool(not run.get("correctness_pass") and run.get("patterns_applied")),
            score_delta=score_delta,
        )
    effectiveness["updated_at"] = int(time.time())
    save_pattern_effectiveness(repo_root, effectiveness)


def record_pattern_family_success(repo: Path, learned_runs: list[dict]) -> None:
    memory = load_script_pattern_memory(repo)
    updated = False
    for pattern in memory.get("patterns", []):
        if not isinstance(pattern, dict):
            continue
        family = pattern.get("family", pattern.get("pattern_type", ""))
        successes = 0
        for run in learned_runs:
            if run.get("correctness_pass") and family in run.get("patterns_applied", []):
                successes += 1
        if successes:
            pattern["success_count"] = int(pattern.get("success_count", 0) or 0) + successes
            updated = True
    if updated:
        save_script_pattern_memory(repo, memory)


def run_pattern_learning_eval(repo: Path, tasks_path: str | None, pattern_repo: Path | None = None) -> dict:
    tasks, resolved_tasks_path = load_pattern_eval_tasks(repo, tasks_path)
    selected_pattern_repo = pattern_repo or repo
    memory = load_script_pattern_memory(selected_pattern_repo)
    effectiveness = load_pattern_effectiveness(selected_pattern_repo)
    baseline_runs: list[dict] = []
    learned_runs: list[dict] = []
    for task in tasks:
        baseline_runs.append(run_pattern_eval_mode(repo, task, memory, use_learned_patterns=False))
        learned_runs.append(run_pattern_eval_mode(repo, task, memory, use_learned_patterns=True, pattern_repo_selection={"selected": "default", "path": selected_pattern_repo, "reason": "eval pattern repo", "confidence": "medium", "tags": []}, pattern_effectiveness=effectiveness, config={}))
    record_pattern_family_success(selected_pattern_repo, learned_runs)
    record_pattern_effectiveness(selected_pattern_repo, learned_runs, baseline_runs)
    summary = summarize_pattern_eval_comparison(baseline_runs, learned_runs)
    result = {
        "tasks_path": str(resolved_tasks_path),
        "task_count": len(tasks),
        "baseline_runs": baseline_runs,
        "learned_runs": learned_runs,
        "summary": summary,
        "ran_at": int(time.time()),
    }
    append_pattern_eval_history(repo, result)
    return result


def print_pattern_eval_report(result: dict) -> None:
    print("=== PATTERN LEARNING EVAL ===")
    print(f"tasks_path: {result.get('tasks_path', '')}")
    print(f"task_count: {result.get('task_count', 0)}")
    for label, runs in [("baseline", result.get("baseline_runs", [])), ("learned", result.get("learned_runs", []))]:
        print(f"\n--- {label.upper()} ---")
        for record in runs:
            print(f"task_id: {record.get('task_id', '')}")
            print(f"task_type: {record.get('task_type', '')}")
            print(f"patterns considered: {record.get('patterns_considered', [])}")
            print(f"patterns rejected: {record.get('patterns_rejected', [])}")
            print(f"patterns applied: {record.get('patterns_applied', [])}")
            print(f"validation command chosen: {record.get('validation_command', '')}")
            print(f"validation success: {record.get('validation_success')}")
            print(f"correctness_pass: {record.get('correctness_pass')}")
            print(f"correctness_reason: {record.get('correctness_reason', '')}")
            print(f"files changed: {record.get('files_changed', [])}")
            print(f"final outcome: {record.get('final_outcome', '')}")
            print(f"style match: {record.get('style_match_summary', '')}")
            if label == "learned":
                print(format_script_pattern_transparency(record.get("selection", {})))
            print(f"score: {record.get('score', {}).get('total', 0)}")
    print("\n=== PATTERN EVAL SUMMARY ===")
    summary = result.get("summary", {})
    print(f"baseline_pass_rate: {summary.get('baseline_pass_rate', 0)}")
    print(f"learned_pass_rate: {summary.get('learned_pass_rate', 0)}")
    print(f"baseline_correctness_rate: {summary.get('baseline_correctness_rate', 0)}")
    print(f"learned_correctness_rate: {summary.get('learned_correctness_rate', 0)}")
    print(f"baseline_average_score: {summary.get('baseline_average_score', 0)}")
    print(f"learned_average_score: {summary.get('learned_average_score', 0)}")
    print(f"tasks_improved: {summary.get('tasks_improved', [])}")
    print(f"tasks_regressed: {summary.get('tasks_regressed', [])}")
    print(f"over_application_cases: {summary.get('over_application_cases', [])}")
    print(f"tasks_changed_outcome: {summary.get('tasks_changed_outcome', [])}")


def detect_blocked_state(
    failure_type: str,
    latest_test_output: str,
    relevant_context: dict | None,
    precommit_rejection_count: int,
    repeated_failure_count: int,
    zero_score_streak: int,
    likely_rate_limit_hits: int = 0,
) -> dict | None:
    remote_blocked = REMOTE_EXECUTION_STATE.get("blocked")
    if remote_blocked:
        return remote_blocked
    text = (latest_test_output or "").lower()
    selected = (relevant_context or {}).get("selected", [])
    top_score = selected[0].get("score", 0) if selected else 0

    if precommit_rejection_count >= 2:
        return {
            "reason": "repeated candidate validation rejection",
            "evidence": "multiple pre-commit candidate validations were rejected",
            "needs": "a materially different patch approach or manual review of the generated diff",
            "action": "Inspect the diff, narrow the test target, or rerun with --mode deep.",
        }
    if likely_rate_limit_hits >= 2:
        return {
            "reason": "likely external API or rate-limit issue",
            "evidence": "subprocess output repeatedly matched rate-limit or temporary-unavailability signals",
            "needs": "time for cooldown or higher API quota / service recovery",
            "action": "Wait briefly, lower request volume, or rerun after the external service limit resets.",
        }
    if any(token in text for token in ["no module named pytest", "command not found", "not installed", "executable file not found"]):
        return {
            "reason": "repo/environment setup issue",
            "evidence": "the test environment is missing tooling or dependencies",
            "needs": "a working environment that can run the configured test command",
            "action": "Install missing dependencies and rerun the failing test command manually first.",
        }
    if any(token in text for token in ["api key", "credential", "unauthorized", "forbidden", "connection refused", "timed out", "dns", "service unavailable"]):
        return {
            "reason": "external service or credential dependency issue",
            "evidence": "the failure output points to network/service/authentication problems",
            "needs": "working credentials or reachable external dependencies",
            "action": "Verify credentials, service availability, and runtime dependencies outside the agent.",
        }
    if failure_type == FAILURE_IMPORT_ERROR and any(token in text for token in ["modulenotfounderror", "importerror"]):
        return {
            "reason": "missing dependency or import failure outside editable repo code",
            "evidence": "imports are failing before the target code can run",
            "needs": "the missing dependency or import path to exist in the environment or repo",
            "action": "Install the missing package or fix the import path, then rerun the failing test.",
        }
    if not selected or top_score < 6:
        return {
            "reason": "no relevant files identified with enough confidence",
            "evidence": "file ranking did not find a strong target for the failure",
            "needs": "a narrower failing test, better traceback, or more concrete symbol information",
            "action": "Rerun on a narrower test target or inspect the traceback and relevant files manually.",
        }
    if repeated_failure_count >= MAX_REPEATED_FAILURES or (zero_score_streak >= 2 and repeated_failure_count >= 2):
        return {
            "reason": "repeated stagnation without meaningful progress",
            "evidence": "the same or equivalent failure persisted across multiple attempts",
            "needs": "a different strategy or additional human guidance",
            "action": "Rerun with --mode deep, narrow the test target, or review the last diff manually.",
        }
    return None


def format_blocked_message(blocked: dict) -> str:
    return "\n".join(
        [
            f"BLOCKED: {blocked.get('reason', 'unknown reason')}",
            f"Evidence: {blocked.get('evidence', 'not available')}",
            f"Need: {blocked.get('needs', 'not available')}",
            f"Suggested action: {blocked.get('action', 'not available')}",
        ]
    )


def resolve_run_settings(args, require_test_cmd: bool = True) -> tuple[Path, str, int, int, str, str, Path, Path, dict, str]:
    global CURRENT_VALIDATION_PLAN
    cwd = Path.cwd().resolve()
    config, config_path = load_agent_config(args.config, cwd)
    recent_state = load_recent_state()
    recent_runs = [item for item in recent_state.get("recent_runs", []) if isinstance(item, dict)]
    last_run = recent_runs[-1] if recent_runs else {}
    last_failed_run = next((item for item in reversed(recent_runs) if item.get("success") is False), {})
    last_successful_run = next((item for item in reversed(recent_runs) if item.get("success") is True), {})
    target = (args.target or last_run.get("target", "") or config.get("target", "")).strip()

    if getattr(args, "script", ""):
        script_path = Path(args.script).expanduser().resolve()
        if not script_path.exists():
            raise SystemExit(f"Missing script path: {script_path}")
        repo = detect_repo_for_script(script_path)
        CURRENT_VALIDATION_PLAN = build_script_validation_plan(repo, script_path)
        test_cmd = args.test_cmd or CURRENT_VALIDATION_PLAN.get("primary_command", "")
        mode = args.mode or "quick"
        mode_source = "script discovery" if not args.mode else "explicit"
        mode_settings = RUN_MODES.get(mode, RUN_MODES["quick"])
        max_steps = args.max_steps if args.max_steps is not None else int(config.get("max_steps", mode_settings["max_steps"]))
        max_file_chars = (
            args.max_file_chars if args.max_file_chars is not None else int(config.get("max_file_chars", mode_settings["max_file_chars"]))
        )
        safety_settings = {
            "HTTP_PROXY": args.http_proxy or config.get("HTTP_PROXY") or os.environ.get("HTTP_PROXY", ""),
            "HTTPS_PROXY": args.https_proxy or config.get("HTTPS_PROXY") or os.environ.get("HTTPS_PROXY", ""),
            "ALL_PROXY": config.get("ALL_PROXY") or os.environ.get("ALL_PROXY", ""),
            "run_budget": args.api_budget_run if args.api_budget_run is not None else int(config.get("api_budget_run", 0) or 0),
            "attempt_budget": (
                args.api_budget_attempt if args.api_budget_attempt is not None else int(config.get("api_budget_attempt", 0) or 0)
            ),
        }
        return repo, test_cmd, max_steps, max_file_chars, mode, mode_source, config_path, script_state_path(RECENT_STATE_FILE_NAME), safety_settings, ""

    CURRENT_VALIDATION_PLAN = {}
    repo_value = args.repo
    if not repo_value and (args.last or args.continue_run):
        repo_value = last_run.get("repo", "")
    if not repo_value and args.from_last_failure:
        repo_value = last_failed_run.get("repo", "")
    if not repo_value and not args.repo and not args.test_cmd and not args.test_cmd_positional:
        repo_value = last_failed_run.get("repo", "") or last_successful_run.get("repo", "")
    if not repo_value and not args.repo and not args.test_cmd and not args.test_cmd_positional and recent_runs:
        repo_value = last_run.get("repo", "")
    if not repo_value:
        repo_value = config.get("repo", "")
    if not repo_value and not target:
        detected = detect_current_repo(cwd)
        repo_value = str(detected) if detected else ""

    if not repo_value:
        raise SystemExit("Could not determine repo. Use --repo or run inside a git repository.")

    repo = Path(repo_value).expanduser().resolve()

    mode = args.mode or ""
    mode_source = "explicit"
    if not mode and (args.last or args.continue_run):
        mode = last_run.get("mode", "")
        mode_source = "recent"
    if not mode and args.from_last_failure:
        mode = last_failed_run.get("mode", "")
        mode_source = "recent"
    if not mode and recent_runs and not args.repo and not args.test_cmd and not args.test_cmd_positional:
        mode = last_run.get("mode", "")
        mode_source = "recent"

    positional_cmd = " ".join(args.test_cmd_positional).strip() if isinstance(args.test_cmd_positional, list) else (args.test_cmd_positional or "")
    explicit_test_cmd = bool(args.test_cmd or positional_cmd)
    test_cmd = args.test_cmd or positional_cmd or ""
    if not test_cmd and (args.last or args.reuse_last_test or args.continue_run):
        test_cmd = last_run.get("test_cmd", "")
    if not test_cmd and args.from_last_failure:
        test_cmd = last_failed_run.get("test_cmd", "")
    if not test_cmd and not explicit_test_cmd:
        test_cmd = last_failed_run.get("test_cmd", "") or last_run.get("test_cmd", "") or last_successful_run.get("test_cmd", "")
    if not test_cmd:
        test_cmd = config.get("test_cmd", "")
    if not test_cmd and require_test_cmd:
        raise SystemExit(missing_test_command_message(repo))

    if not mode and target:
        mode = "safe"
        mode_source = "remote default"
    if not mode and test_cmd:
        mode, mode_source = infer_mode(test_cmd, recent_runs, repo)
    if not mode:
        mode = "safe"
        mode_source = "default"
    if mode_source == "explicit":
        mode_source = "explicit"
    mode_settings = RUN_MODES.get(mode, RUN_MODES["safe"])

    max_steps = args.max_steps if args.max_steps is not None else int(config.get("max_steps", mode_settings["max_steps"]))
    max_file_chars = (
        args.max_file_chars if args.max_file_chars is not None else int(config.get("max_file_chars", mode_settings["max_file_chars"]))
    )
    safety_settings = {
        "HTTP_PROXY": args.http_proxy or config.get("HTTP_PROXY") or os.environ.get("HTTP_PROXY", ""),
        "HTTPS_PROXY": args.https_proxy or config.get("HTTPS_PROXY") or os.environ.get("HTTPS_PROXY", ""),
        "ALL_PROXY": config.get("ALL_PROXY") or os.environ.get("ALL_PROXY", ""),
        "run_budget": args.api_budget_run if args.api_budget_run is not None else int(config.get("api_budget_run", 0) or 0),
        "attempt_budget": (
            args.api_budget_attempt if args.api_budget_attempt is not None else int(config.get("api_budget_attempt", 0) or 0)
        ),
    }
    return repo, test_cmd, max_steps, max_file_chars, mode, mode_source, config_path, script_state_path(RECENT_STATE_FILE_NAME), safety_settings, target


def load_recent_run_metrics(repo: Path, limit: int = 6) -> list[dict]:
    path = state_storage_path(repo, METRICS_FILE_NAME)
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    runs = loaded.get("runs", []) if isinstance(loaded, dict) else []
    if not isinstance(runs, list):
        return []
    return [run for run in runs if isinstance(run, dict)][-limit:]


def analyze_run_comparison(runs: list[dict]) -> str:
    if len(runs) < 2:
        return "Run comparison: insufficient history."

    midpoint = max(1, len(runs) // 2)
    older = runs[:midpoint]
    newer = runs[midpoint:]

    def success_rate(items: list[dict]) -> float:
        return sum(1 for item in items if item.get("success")) / len(items)

    def avg_attempts(items: list[dict]) -> float:
        values = [item.get("attempts_to_success") or item.get("total_attempts", 0) for item in items]
        return sum(values) / len(values) if values else 0.0

    def rejection_rate(items: list[dict]) -> float:
        total_candidates = 0
        total_rejections = 0
        for item in items:
            for attempt in item.get("attempts", []):
                total_candidates += attempt.get("candidate_count", 0)
                total_rejections += attempt.get("candidate_rejections", 0)
        return (total_rejections / total_candidates) if total_candidates else 0.0

    old_success = success_rate(older)
    new_success = success_rate(newer)
    old_attempts = avg_attempts(older)
    new_attempts = avg_attempts(newer)
    old_rejections = rejection_rate(older)
    new_rejections = rejection_rate(newer)

    improved = (
        new_success > old_success
        or (new_success == old_success and new_attempts < old_attempts)
        or (new_success == old_success and new_rejections < old_rejections)
    )
    regressed = (
        new_success < old_success
        or (new_success == old_success and new_attempts > old_attempts)
        or (new_success == old_success and new_rejections > old_rejections)
    )
    verdict = "performance improved" if improved and not regressed else "performance regressed" if regressed and not improved else "performance mixed"

    return (
        "Run comparison: "
        f"{verdict}. "
        f"success_rate {old_success:.2f}->{new_success:.2f}, "
        f"avg_attempts {old_attempts:.2f}->{new_attempts:.2f}, "
        f"candidate_rejection_rate {old_rejections:.2f}->{new_rejections:.2f}."
    )


def summarize_run_metrics(run_metrics: dict) -> str:
    attempts = run_metrics.get("attempts", [])
    total_attempts = run_metrics.get("total_attempts", len(attempts))
    success = run_metrics.get("success", False)
    attempts_to_success = run_metrics.get("attempts_to_success")
    candidate_counts = [attempt.get("candidate_count", 0) for attempt in attempts]
    total_candidates = sum(candidate_counts)
    total_rejected = sum(attempt.get("candidate_rejections", 0) for attempt in attempts)
    rejection_rate = (total_rejected / total_candidates) if total_candidates else 0.0
    strategy_success: dict[str, int] = {}
    for attempt in attempts:
        if attempt.get("hypothesis_result") == "confirmed":
            strategy = attempt.get("strategy_type", "")
            if strategy:
                strategy_success[strategy] = strategy_success.get(strategy, 0) + 1
    most_successful = sorted(strategy_success.items(), key=lambda item: (-item[1], item[0]))[:3]
    lines = [
        "=== RUN METRICS ===",
        f"success: {success}",
        f"total_attempts: {total_attempts}",
        f"attempts_to_success: {attempts_to_success if attempts_to_success is not None else 'n/a'}",
        f"avg_candidates_evaluated: {(total_candidates / len(attempts)) if attempts else 0:.2f}",
        f"candidate_rejection_rate: {rejection_rate:.2f}",
        f"rollback_count: {run_metrics.get('rollback_count', 0)}",
        f"proxy_enabled: {run_metrics.get('proxy_enabled', False)}",
        f"likely_rate_limit_hits: {run_metrics.get('likely_rate_limit_hits', 0)}",
        f"cooldowns_triggered: {run_metrics.get('cooldowns_triggered', 0)}",
        f"remote_blocked_kind: {run_metrics.get('remote_blocked_kind') or 'none'}",
        f"blocked_reason: {run_metrics.get('blocked_reason') or 'none'}",
        "most_successful_strategies: "
        + (", ".join(f"{name}={count}" for name, count in most_successful) if most_successful else "none"),
    ]
    return "\n".join(lines)


def format_run_artifact_summary(repo: Path, recent_state_path: Path, config_path: Path, artifact_dir: Path | None = None, target: str = "") -> str:
    return (
        "Artifacts: "
        + (f"run={artifact_dir}, " if artifact_dir else "")
        + f"metrics={state_storage_path(repo, METRICS_FILE_NAME)}, "
        + f"memory={state_storage_path(repo, MEMORY_FILE_NAME)}, "
        + f"recent={recent_state_path}, "
        + f"config={config_path}"
        + (f", target={target}" if target else "")
    )


def create_run_artifact_dir(repo: Path) -> Path:
    root = state_storage_path(repo, RUN_ARTIFACTS_DIR_NAME)
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / time.strftime("%Y%m%d-%H%M%S")
    suffix = 0
    candidate = run_dir
    while candidate.exists():
        suffix += 1
        candidate = root / f"{run_dir.name}-{suffix}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def write_run_artifacts(
    repo: Path,
    artifact_dir: Path,
    run_metrics: dict,
    summary_text: str,
    comparison_text: str,
    rerun_cmd: str,
    continue_cmd: str,
    full_suite_cmd: str,
) -> None:
    summary = {
        "run_metrics": run_metrics,
        "summary": summary_text,
        "comparison": comparison_text,
        "rerun_cmd": rerun_cmd,
        "continue_cmd": continue_cmd,
        "full_suite_cmd": full_suite_cmd,
        "diff_path": str(artifact_dir / "diff.patch"),
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "metrics.json").write_text(json.dumps(run_metrics, indent=2, sort_keys=True) + "\n")
    (artifact_dir / "diff.patch").write_text(filtered_git_diff_output(repo))
    log_text = "\n".join(
        [
            summary_text,
            comparison_text,
            f"rerun: {rerun_cmd}",
            f"continue: {continue_cmd}",
            f"full_suite: {full_suite_cmd}",
            f"inspect_diff: git -C {repo} diff",
        ]
    )
    (artifact_dir / "log.txt").write_text(log_text + "\n")


def build_action_summary(repo: Path, test_cmd: str, mode: str, outcome: str, dry_run: bool) -> tuple[str, str, str, str]:
    script = Path(__file__).resolve()
    rerun_cmd = f'python {script} --repo "{repo}" --mode {mode} --test-cmd "{test_cmd}"'
    continue_cmd = f'python {script} --continue'
    full_suite_cmd = f'python {script} --repo "{repo}" --mode {mode} --test-cmd "pytest -q"'
    inspect_cmd = f'git -C "{repo}" diff'
    lines = ["Next actions:"]
    if outcome == "success":
        lines.extend([f"- run full suite: {full_suite_cmd}", f"- inspect diff: {inspect_cmd}"])
    elif outcome == "dry-run":
        lines.extend([f"- rerun without --dry-run: {rerun_cmd}", f"- inspect diff: {inspect_cmd}"])
    else:
        lines.extend([f"- rerun deeper: {rerun_cmd} --mode deep", f"- narrow the target: {rerun_cmd}"])
    lines.extend([f"- continue: {continue_cmd}", f"- rerun: {rerun_cmd}"])
    text = "\n".join(lines)
    return text, rerun_cmd, continue_cmd, full_suite_cmd


def prompt_yes_no(question: str, default: bool = False) -> bool:
    if not sys.stdin.isatty():
        return False
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except EOFError:
        return False
    if not answer:
        return default
    return answer in {"y", "yes"}


def interactive_prompt_text(question: str, default: str = "") -> str:
    suffix = f" [Enter={default}] " if default else " "
    while True:
        try:
            answer = input(question + suffix).strip()
        except EOFError:
            return default
        if answer == "?":
            print("help: enter a value or press Enter to accept the default")
            continue
        return answer or default


def interactive_prompt_yes_no(question: str, default: bool = False) -> bool:
    suffix = " [Enter=Y/n] " if default else " [y/Enter=N] "
    while True:
        try:
            answer = input(question + suffix).strip().lower()
        except EOFError:
            return default
        if answer == "?":
            print("help: enter y or n, or press Enter to accept the default")
            continue
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Invalid selection. Enter y, n, or ? for help.")


def interactive_prompt_select(question: str, options: list[tuple[str, str]], default_key: str = "") -> str:
    default_index = 1
    for idx, (key, _) in enumerate(options, start=1):
        if key == default_key:
            default_index = idx
            break
    while True:
        print(question)
        for idx, (_, label) in enumerate(options, start=1):
            marker = " [Enter]" if idx == default_index else ""
            print(f"[{idx}] {label}{marker}")
        choice = interactive_prompt_text("Select an option:", str(default_index))
        if choice == "?":
            print("help: enter the number or key for one option, or press Enter for the default")
            continue
        if choice in {"\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"}:
            return options[default_index - 1][0]
        if choice.isdigit():
            numeric = int(choice)
            if 1 <= numeric <= len(options):
                return options[numeric - 1][0]
        for key, _label in options:
            if choice == key:
                return key
        print("Invalid selection. Choose one of the listed numbers.")


def build_interactive_common_args(session: dict, repo: str = "", include_proxy: bool = True, include_output: bool = False) -> list[str]:
    args: list[str] = []
    repo_value = str(repo or session.get("repo") or "").strip()
    if repo_value:
        args.extend(["--repo", repo_value])
    if include_proxy:
        if str(session.get("http_proxy") or "").strip():
            args.extend(["--http-proxy", str(session["http_proxy"]).strip()])
        if str(session.get("https_proxy") or "").strip():
            args.extend(["--https-proxy", str(session["https_proxy"]).strip()])
    output_value = str(session.get("output") or "human").strip()
    if include_output and output_value and output_value != "human":
        args.extend(["--output", output_value])
    return args


def format_interactive_cli_command(args: list[str]) -> str:
    script_name = Path(__file__).name
    return "python " + " ".join([shlex.quote(script_name), *[shlex.quote(item) for item in args]])


def interactive_standard_scaffold_note() -> str:
    return "This workflow is scaffolded and routed correctly; deeper prompts will be added next."


def interactive_section(title: str) -> None:
    print(f"\n=== {title.upper()} ===")


def interactive_workflow_mode(session: dict) -> str:
    mode = str(session.get("interaction_mode") or "guided").strip().lower()
    return mode if mode in {"guided", "quick"} else "guided"


def classify_config_file(config_path: Path, content: str = "") -> dict:
    path_text = config_path.as_posix().lower()
    name = config_path.name.lower()
    lowered = content.lower()
    if name == "php.ini" or path_text.endswith("/php.ini"):
        return {"config_type": "php_ini", "classification_source": "path_rule", "confidence": "high"}
    if "/pool.d/" in path_text or name.endswith(".pool.conf"):
        return {"config_type": "php_fpm_pool", "classification_source": "path_rule", "confidence": "high"}
    if "nginx" in path_text or "/conf.d/" in path_text:
        return {"config_type": "nginx", "classification_source": "path_rule", "confidence": "high"}
    if any(token in lowered for token in ["pm =", "listen =", "pm.max_children", "php-fpm", "[www]"]):
        return {"config_type": "php_fpm_pool", "classification_source": "content_pattern", "confidence": "high"}
    if any(token in lowered for token in ["memory_limit", "upload_max_filesize", "post_max_size", "[php]"]):
        return {"config_type": "php_ini", "classification_source": "content_pattern", "confidence": "high"}
    if any(token in lowered for token in ["proxy_pass", "upstream ", "server {", "location /", "http {", "events {"]):
        config_type = "nginx" if any(token in lowered for token in ["server {", "location /", "http {", "events {"]) else "reverse_proxy"
        return {"config_type": config_type, "classification_source": "content_pattern", "confidence": "medium"}
    if name.endswith(".conf"):
        return {"config_type": "reverse_proxy", "classification_source": "extension", "confidence": "low"}
    if name.endswith(".ini"):
        return {"config_type": "php_ini", "classification_source": "extension", "confidence": "low"}
    return {"config_type": "reverse_proxy", "classification_source": "fallback", "confidence": "low"}


def default_config_validation_command(config_path: Path, config_type: str) -> str:
    normalized = str(config_type or "auto").strip()
    quoted_path = shlex.quote(str(config_path))
    if normalized in {"nginx", "reverse_proxy"}:
        return f"nginx -t -c {quoted_path}"
    if normalized == "php_fpm_pool":
        return "php-fpm -t"
    if normalized == "php_ini":
        return f"php -n -c {quoted_path} -m"
    return ""


def parse_config_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";", "[")):
        return None
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    return key.strip(), value.strip()


def derive_config_style_hints(content: str, config_type: str) -> dict:
    hints = {
        "spaces_around_equals": True,
        "indent": "    ",
        "config_type": config_type,
    }
    for line in content.splitlines():
        if "=" in line and parse_config_assignment(line):
            hints["spaces_around_equals"] = " = " in line
            break
    for line in content.splitlines():
        if line.startswith((" ", "\t")) and line.strip():
            indent_match = re.match(r"^([ \t]+)", line)
            if indent_match:
                hints["indent"] = indent_match.group(1)
                break
    return hints


def resolve_config_pattern_source(pattern_repo_value: str | None) -> Path | None:
    selected = str(pattern_repo_value or "").strip()
    if not selected or selected in {"auto", "none"}:
        return None
    if selected == "default":
        return default_pattern_repo_path()
    candidate = normalize_pattern_repo_path(selected)
    return candidate if candidate.exists() else None


def load_config_style_hints(pattern_repo_value: str | None, config_type: str) -> dict:
    root = resolve_config_pattern_source(pattern_repo_value)
    if root is None or not root.exists():
        return {}
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        candidates.append(path)
        if len(candidates) >= 30:
            break
    for candidate in candidates:
        try:
            content = candidate.read_text()
        except OSError:
            continue
        detected = classify_config_file(candidate, content)
        if detected.get("config_type") != config_type:
            continue
        hints = derive_config_style_hints(content, config_type)
        hints["source_path"] = str(candidate)
        hints["confidence"] = detected.get("confidence", "low")
        return hints
    return {}


def normalize_ini_assignment_line(line: str, *, spaces_around_equals: bool) -> str:
    parsed = parse_config_assignment(line)
    if not parsed:
        return line.rstrip()
    key, value = parsed
    separator = " = " if spaces_around_equals else "="
    return f"{key}{separator}{value}"


def normalize_common_config_lines(content: str) -> list[str]:
    lines: list[str] = []
    blank_pending = False
    for raw in content.splitlines():
        stripped = raw.rstrip()
        if not stripped:
            if not blank_pending:
                lines.append("")
            blank_pending = True
            continue
        blank_pending = False
        lines.append(stripped)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def cleanup_config_content(content: str, config_type: str, style_hints: dict | None = None) -> str:
    hints = style_hints or {}
    spaces_around_equals = bool(hints.get("spaces_around_equals", True))
    lines = normalize_common_config_lines(content)
    normalized: list[str] = []
    for line in lines:
        if config_type in {"php_ini", "php_fpm_pool"}:
            normalized.append(normalize_ini_assignment_line(line, spaces_around_equals=spaces_around_equals))
        else:
            normalized.append(line)
    return "\n".join(normalized).rstrip() + "\n"


def align_config_content(content: str, config_type: str, style_hints: dict | None = None) -> str:
    return cleanup_config_content(content, config_type, style_hints=style_hints)


def generate_config_template(config_type: str, config_path: Path, style_hints: dict | None = None) -> str:
    hints = style_hints or {}
    indent = str(hints.get("indent") or "    ")
    spaces_around_equals = bool(hints.get("spaces_around_equals", True))
    separator = " = " if spaces_around_equals else "="
    if config_type in {"nginx", "reverse_proxy"}:
        return (
            "server {\n"
            f"{indent}listen 80;\n"
            f"{indent}server_name example.test;\n\n"
            f"{indent}location / {{\n"
            f"{indent}{indent}proxy_pass http://127.0.0.1:8080;\n"
            f"{indent}{indent}proxy_set_header Host $host;\n"
            f"{indent}{indent}proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            f"{indent}}}\n"
            "}\n"
        )
    if config_type == "php_ini":
        return (
            "[PHP]\n"
            f"memory_limit{separator}256M\n"
            f"upload_max_filesize{separator}32M\n"
            f"post_max_size{separator}32M\n"
            f"max_execution_time{separator}60\n"
        )
    if config_type == "php_fpm_pool":
        return (
            "[www]\n"
            f"user{separator}www-data\n"
            f"group{separator}www-data\n"
            f"listen{separator}/run/php/php-fpm.sock\n"
            f"pm{separator}dynamic\n"
            f"pm.max_children{separator}10\n"
            f"pm.start_servers{separator}2\n"
            f"pm.min_spare_servers{separator}1\n"
            f"pm.max_spare_servers{separator}3\n"
        )
    return f"# generated config for {config_path.name}\n"


def summarize_config_diff(before: str, after: str, *, limit: int = 12) -> dict:
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    changed_lines = [line for line in diff_lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    return {
        "changed_line_count": len(changed_lines),
        "diff_preview": diff_lines[:limit],
    }


def run_config_validation(repo: Path, command: str) -> dict:
    if not command.strip():
        return {"validation_result": "skipped", "validation_command": "", "output": "", "ok": True}
    code, output = run_subprocess(command, repo, shell=True)
    return {
        "validation_result": "success" if code == 0 else "blocked",
        "validation_command": command,
        "output": output,
        "ok": code == 0,
    }


def detect_config_task_confidence(config_type: str, classification_confidence: str, pattern_source_used: str, validation_command: str) -> str:
    score = 0
    if classification_confidence == "high":
        score += 2
    elif classification_confidence == "medium":
        score += 1
    if pattern_source_used not in {"", "none", "auto"}:
        score += 1
    if validation_command:
        score += 1
    if config_type in {"nginx", "php_ini", "php_fpm_pool"}:
        score += 1
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def run_config_workflow(
    repo: Path,
    *,
    config_path: Path,
    task: str,
    config_type: str = "auto",
    compare_path: str = "",
    validation_command: str = "",
    pattern_repo_value: str = "",
) -> dict:
    result = {
        "config_path": str(config_path),
        "task": task,
        "config_type": "unknown",
        "classification_source": "fallback",
        "validation_command": "",
        "validation_result": "not_run",
        "changes_made": False,
        "confidence": "low",
        "pattern_source_used": pattern_repo_value or "none",
        "patterns_applied": [],
        "comparison_target": compare_path or "",
        "compare_summary": "",
        "diff_preview": [],
        "blocked_reason": "",
        "what_happened": "",
        "ok": False,
    }
    original_exists = config_path.exists()
    original_content = ""
    if task != "generate" and not original_exists:
        result["blocked_reason"] = f"Missing config path: {config_path}"
        result["what_happened"] = "The config workflow blocked because the config file does not exist."
        return result
    if original_exists:
        try:
            original_content = config_path.read_text()
        except OSError as exc:
            result["blocked_reason"] = str(exc)
            result["what_happened"] = "The config workflow blocked because the config file could not be read."
            return result
    detected = classify_config_file(config_path, original_content)
    resolved_type = detected.get("config_type", "reverse_proxy") if config_type == "auto" else config_type
    result["config_type"] = str(resolved_type)
    result["classification_source"] = str(detected.get("classification_source", "fallback"))
    style_hints = load_config_style_hints(pattern_repo_value, resolved_type)
    if compare_path and task in {"align", "generate"}:
        reference_path = Path(compare_path).expanduser()
        if not reference_path.is_absolute():
            reference_path = (repo / reference_path).resolve()
        if not reference_path.exists():
            result["blocked_reason"] = f"Missing comparison config: {reference_path}"
            result["what_happened"] = "The config workflow blocked because the style reference file does not exist."
            return result
        try:
            reference_content = reference_path.read_text()
        except OSError as exc:
            result["blocked_reason"] = str(exc)
            result["what_happened"] = "The config workflow blocked because the style reference file could not be read."
            return result
        style_hints = {**style_hints, **derive_config_style_hints(reference_content, resolved_type), "source_path": str(reference_path)}
        result["patterns_applied"] = sorted(set(list(result.get("patterns_applied") or []) + ["reference_config_style"]))
    if style_hints and "trusted_config_style" not in list(result.get("patterns_applied") or []):
        result["patterns_applied"] = list(result.get("patterns_applied") or []) + ["trusted_config_style"]
    effective_validation = validation_command.strip() or ("" if task == "compare" else default_config_validation_command(config_path, resolved_type))
    result["validation_command"] = effective_validation
    result["confidence"] = detect_config_task_confidence(
        resolved_type,
        str(detected.get("confidence", "low")),
        pattern_repo_value or "none",
        effective_validation,
    )
    if task == "compare":
        comparison_target = Path(compare_path).expanduser()
        result["comparison_target"] = str(comparison_target)
        if not comparison_target.exists():
            result["blocked_reason"] = f"Missing comparison config: {comparison_target}"
            result["what_happened"] = "The config workflow blocked because the comparison file does not exist."
            return result
        try:
            comparison_content = comparison_target.read_text()
        except OSError as exc:
            result["blocked_reason"] = str(exc)
            result["what_happened"] = "The config workflow blocked because the comparison file could not be read."
            return result
        diff_summary = summarize_config_diff(original_content, comparison_content)
        result["compare_summary"] = f"{diff_summary['changed_line_count']} line(s) differ"
        result["diff_preview"] = diff_summary["diff_preview"]
        validation = run_config_validation(repo, effective_validation) if effective_validation else {"validation_result": "skipped", "ok": True, "validation_command": "", "output": ""}
        result["validation_result"] = validation["validation_result"]
        result["validation_command"] = validation.get("validation_command", effective_validation)
        result["ok"] = bool(validation.get("ok", True))
        result["what_happened"] = "Compared the two configs and reported the diff summary."
        if not result["ok"]:
            result["blocked_reason"] = str(validation.get("output") or "config validation failed").strip()
        return result
    if task == "validate":
        validation = run_config_validation(repo, effective_validation)
        result["validation_result"] = validation["validation_result"]
        result["ok"] = bool(validation["ok"])
        if not result["ok"]:
            result["blocked_reason"] = str(validation.get("output") or "config validation failed").strip()
            result["what_happened"] = "The config file failed validation."
        else:
            result["what_happened"] = "The config file validated successfully."
        return result
    if task == "generate":
        rendered = generate_config_template(resolved_type, config_path, style_hints=style_hints)
    elif task == "align":
        rendered = align_config_content(original_content, resolved_type, style_hints=style_hints)
    else:
        rendered = cleanup_config_content(original_content, resolved_type, style_hints=style_hints)
    result["changes_made"] = rendered != original_content
    diff_summary = summarize_config_diff(original_content, rendered)
    result["diff_preview"] = diff_summary["diff_preview"]
    if not result["changes_made"] and task != "generate":
        validation = run_config_validation(repo, effective_validation) if effective_validation else {"validation_result": "skipped", "ok": True, "validation_command": "", "output": ""}
        result["validation_result"] = validation["validation_result"]
        result["ok"] = bool(validation["ok"])
        if not result["ok"]:
            result["blocked_reason"] = str(validation.get("output") or "config validation failed").strip()
            result["what_happened"] = "No cleanup changes were kept because validation did not pass."
        else:
            result["what_happened"] = "The config was already aligned with the current cleanup/style rules."
        return result
    created_parent = False
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        created_parent = True
    config_path.write_text(rendered)
    validation = run_config_validation(repo, effective_validation) if effective_validation else {"validation_result": "skipped", "ok": True, "validation_command": "", "output": ""}
    result["validation_result"] = validation["validation_result"]
    result["ok"] = bool(validation["ok"])
    if not validation["ok"]:
        if original_exists:
            config_path.write_text(original_content)
        elif config_path.exists():
            config_path.unlink()
            if created_parent:
                try:
                    config_path.parent.rmdir()
                except OSError:
                    pass
        result["changes_made"] = False
        result["blocked_reason"] = str(validation.get("output") or "config validation failed").strip()
        result["what_happened"] = "The config changes were reverted because validation failed."
        return result
    if task == "generate":
        result["what_happened"] = "Generated a new config file and validated it successfully."
    elif task == "align":
        result["what_happened"] = "Aligned the config with the selected style hints and validated it successfully."
    else:
        result["what_happened"] = "Cleaned up the config and kept the changes because validation passed."
    return result


def print_config_workflow_result(result: dict) -> None:
    print("=== CONFIG WORKFLOW ===")
    print(f"config_path: {result.get('config_path', '')}")
    print(f"config_type: {result.get('config_type', 'unknown')}")
    print(f"task: {result.get('task', '')}")
    print(f"validation_command: {result.get('validation_command', '')}")
    print(f"validation_result: {result.get('validation_result', 'not_run')}")
    print(f"changes_made: {format_bool(bool(result.get('changes_made')))}")
    print(f"confidence: {result.get('confidence', 'low')}")
    print(f"pattern_source_used: {result.get('pattern_source_used', 'none')}")
    print(f"patterns_applied: {result.get('patterns_applied', [])}")
    if result.get("comparison_target"):
        print(f"comparison_target: {result.get('comparison_target')}")
        print(f"compare_summary: {result.get('compare_summary', '')}")
    if result.get("diff_preview"):
        print(f"diff_preview: {result.get('diff_preview')}")
    if result.get("blocked_reason"):
        print(f"blocked_reason: {result.get('blocked_reason')}")
    print(f"what_happened: {result.get('what_happened', '')}")


def print_interactive_header(session: dict) -> None:
    print("\n=== LOCAL FIX AGENT APP ===")
    print(f"default_repo: {session.get('repo') or Path.cwd()}")
    print(f"interaction_mode: {session.get('interaction_mode') or 'guided'}")
    print("navigation: select a workflow, review the action, then choose run, back, or cancel")


def interactive_next_step_prompt() -> str:
    return interactive_prompt_select(
        "Next step:",
        [
            ("run", "Run this action"),
            ("back", "Back to main menu"),
            ("cancel", "Cancel and exit interactive mode"),
        ],
        default_key="run",
    )


def interactive_workflow_registry() -> dict[str, dict]:
    return {
        "fix_validate": {
            "label": "Fix or validate a script",
            "description": "Fix one script and run validation, or validate a script without editing it.",
            "handler": interactive_fix_validate_action,
        },
        "new_script": {
            "label": "Create a new script",
            "description": "Create a new script from learned patterns, optionally using live endpoint evidence when network truth matters.",
            "handler": interactive_new_script_action,
        },
        "publish_current": {
            "label": "Publish current repo state",
            "description": "Safely publish the current repo state through the guarded finalizer path.",
            "handler": interactive_publish_current_action,
        },
        "publish_validated": {
            "label": "Publish last validated run",
            "description": "Use this when a validated state already exists and you want the canonical finalizer flow.",
            "handler": interactive_publish_validated_action,
        },
        "import_training": {
            "label": "Import a script into training",
            "description": "Sanitize, validate, and add a script to the private training repo.",
            "handler": interactive_import_training_action,
        },
        "config_workflow": {
            "label": "Work with a config file",
            "description": "Validate, clean up, compare, align, or generate a config file with real validation commands.",
            "handler": interactive_config_action,
        },
        "inspect_patterns": {
            "label": "Inspect learned patterns",
            "description": "Use this to review learned patterns or pattern sources without changing them.",
            "handler": interactive_inspect_patterns_action,
        },
        "manage_patterns": {
            "label": "Manage patterns",
            "description": "Use this to promote, demote, or forget a pattern or source through the existing controls.",
            "handler": interactive_manage_patterns_action,
        },
        "probe": {
            "label": "Probe API / M3U8 endpoint",
            "description": "Use this when endpoint truth matters for debugging or validation. It is only one workflow in the app.",
            "handler": interactive_probe_action,
        },
        "sync_conflicts": {
            "label": "Sync / repair repo conflicts",
            "description": "Use this to inspect or repair merge-conflict states through the conflict-handling backend.",
            "handler": interactive_sync_conflicts_action,
        },
        "settings": {
            "label": "Settings / advanced options",
            "description": "Use this to update session defaults such as repo path, proxy settings, and output mode.",
            "handler": interactive_update_settings,
        },
        "exit": {
            "label": "Exit",
            "description": "Leave the interactive app.",
            "handler": None,
        },
    }


def print_interactive_action_summary(action: dict) -> None:
    interactive_section("Confirmation summary")
    print(f"workflow: {action.get('workflow') or '(unknown)'}")
    if action.get("description"):
        print(f"when_to_use: {action.get('description')}")
    if action.get("scaffolded"):
        print(f"status: {interactive_standard_scaffold_note()}")
    for key, value in (action.get("inputs") or {}).items():
        print(f"{key}: {value}")
    for note in action.get("notes") or []:
        print(f"note: {note}")
    commands = action.get("commands") or []
    if commands:
        print("command_preview:")
        for item in commands:
            label = str(item.get("label") or "command")
            compact_preview = str(item.get("compact_preview") or label)
            print(f"- {compact_preview}")
        print("equivalent_command:")
        for item in commands:
            label = str(item.get("label") or "command")
            preview_command = str(item.get("preview_command") or "").strip()
            if preview_command:
                print(f"- {label}: {preview_command}")
            else:
                print(f"- {label}: {format_interactive_cli_command(item.get('args') or [])}")


def run_interactive_backend_command(args: list[str]) -> dict:
    run_command = [sys.executable, str(Path(__file__).resolve()), *args]
    completed = subprocess.Popen(
        run_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    captured_lines: list[str] = []
    assert completed.stdout is not None
    for line in completed.stdout:
        print(line, end="")
        captured_lines.append(line)
    return_code = completed.wait()
    return {
        "returncode": return_code,
        "output": "".join(captured_lines),
    }


def interactive_extract_output_value(output: str, key: str) -> str:
    prefix = f"{key}:"
    value = ""
    for line in output.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
    return value


def interactive_preview_shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(item) for item in args)


def interactive_result_status(success: bool) -> str:
    return "success" if success else "blocked"


def interactive_print_result(
    workflow: str,
    *,
    success: bool,
    fields: list[tuple[str, str]],
    what_happened: str,
    blocked_reason: str = "",
    next_step: str = "",
) -> None:
    interactive_section("Workflow result")
    print(f"workflow: {workflow}")
    print(f"status: {interactive_result_status(success)}")
    for key, value in fields:
        print(f"{key}: {value}")
    if blocked_reason and not success:
        print(f"blocked_reason: {blocked_reason}")
    if next_step and not success:
        print(f"next_step: {next_step}")
    print(f"what_happened: {what_happened}")


def interactive_handle_publish_blocked_followup(action: dict) -> str:
    preflight = action.get("result_context", {}).get("preflight", {}) or {}
    analyses = list(preflight.get("blocked_file_analysis") or [])
    if not analyses:
        return "continue"
    while True:
        choice = interactive_prompt_select(
            "Publish is blocked by unstaged files. What next?",
            [
                ("show", "Show file analysis"),
                ("stage_safe", "Stage safe files only"),
                ("show_ignore", "Show ignore/remove suggestions"),
                ("back", "Back to main menu"),
                ("cancel", "Cancel"),
            ],
            default_key="show",
        )
        if choice == "show":
            print("")
            print_publish_block_analysis(analyses, preflight.get("blocked_analysis_summary") or {})
            continue
        if choice == "show_ignore":
            print("=== SUGGESTED IGNORE / REMOVE ACTIONS ===")
            for item in analyses:
                if item.get("recommended_action") in {"remove generated artifact", "leave untracked / do not publish", "inspect manually before staging"}:
                    print(f"- {item.get('path')}: {item.get('recommended_action')}")
                    for command in item.get("recommended_commands") or []:
                        print(f"  {command}")
            continue
        if choice == "stage_safe":
            safe_paths = [str(item.get("path") or "") for item in analyses if item.get("recommended_action") == "stage and include in publish" and item.get("path")]
            if not safe_paths:
                print("No blocked files are safe stage candidates.")
                continue
            if not interactive_prompt_yes_no("Stage the safe publishable files now?", default=False):
                continue
            repo = Path(str(action.get("inputs", {}).get("repo") or Path.cwd())).expanduser()
            code, output = run_subprocess(["git", "add", "--", *safe_paths], repo)
            if code == 0:
                print(f"staged_safe_files: {safe_paths}")
            else:
                print(f"stage_safe_files_failed: {output.strip() or 'git add failed'}")
            continue
        return choice


def render_interactive_fix_validate_result(action: dict, executed: list[dict]) -> None:
    combined_output = "\n".join(str(item.get("output") or "") for item in executed)
    return_code = next((int(item.get("returncode", 0)) for item in reversed(executed)), 0)
    workflow_mode = str(action.get("inputs", {}).get("mode") or "fix_and_validate")
    validation_result = interactive_extract_output_value(combined_output, "validation_result")
    if not validation_result:
        final_summary = interactive_extract_output_value(combined_output, "FINAL")
        if return_code == 0:
            validation_result = "success"
        elif final_summary and "blocked" in final_summary.lower():
            validation_result = "blocked"
        else:
            validation_result = "failed"
    pattern_line = (
        interactive_extract_output_value(combined_output, "learned patterns applied")
        or interactive_extract_output_value(combined_output, "patterns applied")
        or "(not reported)"
    )
    blocked_reason = interactive_extract_output_value(combined_output, "blocked_reason") or interactive_extract_output_value(combined_output, "reason")
    final_summary = interactive_extract_output_value(combined_output, "FINAL")
    validation_command_used = (
        interactive_extract_output_value(combined_output, "validation_command")
        or str(action.get("result_context", {}).get("validation_command_used") or "")
        or str(action.get("inputs", {}).get("validation_choice") or "")
    )
    probing_used = "yes" if bool(action.get("result_context", {}).get("probe_planned")) else "no"
    publish_status = ""
    if final_summary and "publish" in final_summary.lower():
        publish_status = final_summary
    elif workflow_mode == "fix_and_validate":
        publish_status = "(see backend output)"
    plain_english = "The run completed."
    if workflow_mode == "validate_only":
        if validation_result == "success":
            plain_english = "The script was validated successfully."
        elif validation_result == "blocked":
            plain_english = "The run blocked because the validation command failed."
        else:
            plain_english = "The script did not pass validation."
    else:
        if validation_result == "success":
            plain_english = "The agent fixed issues, reran validation, and the script now passes."
        elif validation_result == "blocked":
            plain_english = "The run blocked because the validation command failed."
        else:
            plain_english = "The fix/validation run did not complete successfully."
    if bool(action.get("result_context", {}).get("probe_planned")):
        plain_english += " A network probe was used to inspect the endpoint before validation."
    interactive_print_result(
        action.get("workflow") or "Fix or validate a script",
        success=validation_result == "success",
        fields=[
            ("script_path", action.get("inputs", {}).get("script_path") or "(unknown)"),
            ("validation_result", validation_result),
            ("validation_command_used", validation_command_used or "(auto)"),
            ("patterns_applied", pattern_line),
            ("probing_used", probing_used),
            ("publish_or_finalization_status", publish_status or "(not reported)"),
        ],
        blocked_reason=blocked_reason if validation_result != "success" else "",
        next_step="Check the validation command output above and rerun with a narrower target or a custom command." if validation_result != "success" else "",
        what_happened=plain_english,
    )


def render_interactive_new_script_result(action: dict, executed: list[dict]) -> None:
    combined_output = "\n".join(str(item.get("output") or "") for item in executed)
    return_code = next((int(item.get("returncode", 0)) for item in reversed(executed)), 0)
    result_context = action.get("result_context", {}) or {}
    script_generated = interactive_extract_output_value(combined_output, "script_generated") or "true"
    output_path = interactive_extract_output_value(combined_output, "output_path") or str(action.get("inputs", {}).get("output_path") or "(unknown)")
    pattern_source_used = interactive_extract_output_value(combined_output, "pattern_source_used") or str(action.get("inputs", {}).get("pattern_source") or "default")
    patterns_applied = interactive_extract_output_value(combined_output, "patterns_applied") or "(not reported)"
    probe_used = interactive_extract_output_value(combined_output, "probe_used") or ("yes" if action.get("result_context", {}).get("probe_planned") else "no")
    key_probe_findings = interactive_extract_output_value(combined_output, "key_probe_findings") or "(none)"
    validation_plan = interactive_extract_output_value(combined_output, "validation_plan") or "(not reported)"
    generation_confidence = interactive_extract_output_value(combined_output, "generation_confidence") or "low"
    validation_success = interactive_extract_output_value(combined_output, "validation_success")
    validation_result = interactive_extract_output_value(combined_output, "validation_result") or ("success" if validation_success == "True" else ("blocked" if return_code != 0 else "success"))
    script_kind = interactive_extract_output_value(combined_output, "script_kind") or "local"
    repair_attempted = str(result_context.get("repair_attempted") or "false").lower() in {"true", "1", "yes"}
    repair_result = str(result_context.get("repair_result") or "not_needed")
    publish_result = interactive_extract_output_value(combined_output, "publish_result") or ("not_requested" if not result_context.get("publish_attempted") else "")
    pr_url = interactive_extract_output_value(combined_output, "pr_url") or ""
    success = return_code == 0 and validation_result in {"success", "skipped"}
    blocked_reason = interactive_extract_output_value(combined_output, "validation_output") or interactive_extract_output_value(combined_output, "blocked_reason")
    plain_english = "The new script was created locally and not published."
    if validation_result == "success" and repair_attempted and repair_result == "success":
        plain_english = "Generated the script, repaired validation issues, and the script now passes."
    elif validation_result == "success" and script_kind == "api" and probe_used.lower() == "true":
        plain_english = "Generated a network-aware script using live API probe results and validated it successfully."
    elif validation_result == "success" and script_kind == "m3u8" and probe_used.lower() == "true":
        plain_english = "Generated an HLS/M3U8 utility using playlist inspection and validated it successfully."
    elif validation_result == "success":
        plain_english = "Generated a new CLI script using trusted patterns and validated it successfully."
    elif validation_result == "skipped":
        plain_english = "The new script was created locally, but validation was skipped."
    elif repair_attempted and repair_result != "success":
        plain_english = "Script generation succeeded, but validation failed and the repair pass did not recover it."
    else:
        plain_english = "Script generation succeeded, but validation failed and the run was blocked."
    if publish_result == "success":
        if probe_used.lower() == "true":
            plain_english = "Generated a network-aware script using live endpoint evidence and published it safely."
        else:
            plain_english = "Generated a new script, validated it, and published it safely."
    elif result_context.get("publish_attempted") and publish_result and publish_result != "not_requested":
        plain_english = "The new script was generated and validated, but publish was blocked."
    next_step = ""
    if not success and validation_result != "skipped":
        next_step = "Review the validation failure above, rerun repair on the generated script, or regenerate with a clearer purpose or validation command."
    if result_context.get("publish_attempted") and publish_result and publish_result != "success":
        next_step = "Review the publish blocker above, fix it, then rerun the publish workflow for this repo."
    interactive_print_result(
        action.get("workflow") or "Create a new script",
        success=success,
        fields=[
            ("script_generated", script_generated),
            ("output_path", output_path),
            ("generation_confidence", generation_confidence),
            ("pattern_source_used", pattern_source_used),
            ("patterns_applied", patterns_applied),
            ("probe_used", probe_used),
            ("key_probe_findings", key_probe_findings),
            ("validation_result", validation_result),
            ("validation_plan", validation_plan),
            ("repair_attempted", format_bool(repair_attempted)),
            ("repair_result", repair_result),
            ("publish_result", publish_result or "not_requested"),
            ("pr_url", pr_url or "(none)"),
        ],
        blocked_reason=blocked_reason if not success and validation_result != "skipped" else "",
        next_step=next_step,
        what_happened=plain_english,
    )


def interactive_default_new_script_output(repo: Path, purpose: str, domain_hint: str) -> str:
    words = [token for token in re.findall(r"[a-z0-9]+", purpose.lower()) if token not in {"a", "an", "the", "and", "for", "with", "from", "into"}]
    stem = "_".join(words[:4]) if words else "generated_tool"
    if domain_hint in {"api", "m3u8", "proxy_auth"}:
        prefix = "network"
    elif domain_hint == "cli":
        prefix = "cli"
    else:
        prefix = "generated"
    return str((repo / "scripts" / f"{prefix}_{stem}.py").resolve())


def interactive_new_script_pattern_source(session: dict, quick_mode: bool) -> tuple[str, list[str], str | None]:
    if quick_mode:
        return "auto", [], None
    choice = interactive_prompt_select(
        "Which pattern source should shape the new script?",
        [
            ("auto", "Auto (recommended)"),
            ("default", "Default pattern repo"),
            ("none", "None"),
            ("specific", "Specific repo or path"),
        ],
        default_key="auto",
    )
    if choice == "default":
        return "default", ["--pattern-repo", "default"], "default"
    if choice == "none":
        return "none", ["--pattern-repo", "none"], "none"
    if choice == "specific":
        value = interactive_prompt_text("Pattern repo name or path:")
        return value or "specific", (["--pattern-repo", value] if value else []), value or None
    return "auto", [], None


def interactive_new_script_validation_choice(quick_mode: bool, output_path: Path) -> tuple[str, str, str]:
    if quick_mode:
        return "auto", "", "auto-detect"
    choice = interactive_prompt_select(
        "How should the new script be validated?",
        [
            ("auto", "Auto-detect (recommended)"),
            ("syntax", "Syntax only"),
            ("cli_help", "CLI help check"),
            ("custom", "Enter custom command"),
        ],
        default_key="auto",
    )
    custom_command = ""
    if choice == "custom":
        custom_command = interactive_prompt_text("Custom validation command:")
        return choice, custom_command, custom_command or "custom"
    if choice == "cli_help":
        return choice, "", f"python {output_path.name} --help"
    if choice == "syntax":
        return choice, "", f"python -m py_compile {output_path.name}"
    return choice, "", "auto-detect"


def interactive_plan_new_script_generation(
    repo_path: Path,
    purpose: str,
    output_path: Path,
    *,
    pattern_repo_cli: str | None,
    probe_planned: str,
) -> tuple[dict, dict]:
    config_values, _config_path = load_agent_config(None, repo_path)
    repo_selection = select_pattern_repo(
        config_values,
        pattern_repo_cli,
        "new-script",
        purpose,
        script_path=output_path,
    )
    selection, repo_selection = resolve_pattern_selection(
        config_values,
        repo_selection,
        "new-script",
        purpose,
        script_path=output_path,
    )
    preview_plan = build_new_script_generation_plan(
        repo_path,
        output_path,
        purpose,
        selection,
        repo_selection,
        probe_result=None,
    )
    if probe_planned != "no":
        preview_plan["probe_used"] = True
        preview_plan["probe_summary"] = {"used": True, "summary": probe_planned, "details": [], "key_findings": []}
    return selection, preview_plan


def interactive_new_script_failure_handler(action: dict, executed: list[dict], failure: dict, run_item) -> dict:
    result_context = action.setdefault("result_context", {})
    if not result_context.get("repair_allowed"):
        result_context["repair_attempted"] = False
        result_context["repair_result"] = "not_allowed"
        return {"recovered": False, "returncode": int(failure.get("returncode", 1))}
    if not bool(action.get("inputs", {}).get("output_path")):
        result_context["repair_attempted"] = False
        result_context["repair_result"] = "not_possible"
        return {"recovered": False, "returncode": int(failure.get("returncode", 1))}
    failure_output = str(failure.get("output") or "")
    if "script_generated: true" not in failure_output and not Path(str(action.get("inputs", {}).get("output_path") or "")).exists():
        result_context["repair_attempted"] = False
        result_context["repair_result"] = "not_possible"
        return {"recovered": False, "returncode": int(failure.get("returncode", 1))}
    repair_command = dict(result_context.get("repair_command") or {})
    if not repair_command:
        result_context["repair_attempted"] = False
        result_context["repair_result"] = "not_configured"
        return {"recovered": False, "returncode": int(failure.get("returncode", 1))}
    result_context["repair_attempted"] = True
    repair_result = run_item(repair_command)
    if repair_result.get("returncode") == 0:
        result_context["repair_result"] = "success"
        return {"recovered": True, "executed": [repair_result]}
    result_context["repair_result"] = "failed"
    return {"recovered": False, "executed": [repair_result], "returncode": int(repair_result.get("returncode", 1))}


def interactive_new_script_post_success_handler(action: dict, executed: list[dict], run_item) -> dict:
    result_context = action.setdefault("result_context", {})
    combined_output = "\n".join(str(item.get("output") or "") for item in executed)
    validation_result = interactive_extract_output_value(combined_output, "validation_result") or ("success" if interactive_extract_output_value(combined_output, "validation_success") == "True" else "")
    if validation_result != "success":
        result_context["publish_attempted"] = False
        return {"returncode": 0}
    if not interactive_prompt_yes_no("Publish this new script now?", default=False):
        result_context["publish_attempted"] = False
        return {"returncode": 0}
    publish_command = dict(result_context.get("publish_command") or {})
    if not publish_command:
        result_context["publish_attempted"] = False
        return {"returncode": 0}
    result_context["publish_attempted"] = True
    publish_result = run_item(publish_command)
    return {"executed": [publish_result], "returncode": int(publish_result.get("returncode", 0))}


def interactive_new_script_action(session: dict) -> dict:
    session_mode = interactive_workflow_mode(session)
    workflow_mode = interactive_prompt_select(
        "Run this workflow in which mode?",
        [("guided", "Guided (recommended)"), ("quick", "Quick")],
        default_key=session_mode,
    )
    quick_mode = workflow_mode == "quick"
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    repo_path = Path(repo).expanduser()
    purpose = interactive_prompt_text("What should the new script do?")
    domain_choice = "auto"
    if not quick_mode:
        domain_choice = interactive_prompt_select(
            "Which script domain best fits?",
            [
                ("auto", "Auto-detect (recommended)"),
                ("local", "Local utility"),
                ("cli", "CLI tool"),
                ("api", "API client"),
                ("m3u8", "M3U8 / HLS utility"),
                ("proxy_auth", "Proxy/auth utility"),
                ("parser", "Parser / transformer"),
                ("other", "Other"),
            ],
            default_key="auto",
        )
    output_default = interactive_default_new_script_output(repo_path, purpose, domain_choice) if purpose else str((repo_path / "scripts" / "generated_tool.py").resolve())
    output_path = interactive_prompt_text("Output path:", output_default)
    resolved_output = Path(output_path).expanduser()
    if not resolved_output.is_absolute():
        resolved_output = (repo_path / resolved_output).resolve()
    output_display = relative_script_path(repo_path, resolved_output) if str(resolved_output).startswith(str(repo_path)) else str(resolved_output)
    pattern_source, pattern_args, pattern_repo_cli = interactive_new_script_pattern_source(session, quick_mode)
    intent = classify_new_script_network_intent(purpose, resolved_output)
    effective_kind = str(intent.get("kind") or "local")
    if domain_choice == "api":
        effective_kind = "api"
    elif domain_choice == "m3u8":
        effective_kind = "m3u8"
    elif domain_choice == "proxy_auth":
        effective_kind = "api"
    elif domain_choice in {"local", "cli", "parser", "other"}:
        effective_kind = "local"
    should_offer_probe = effective_kind in {"api", "m3u8"} or (domain_choice == "proxy_auth") or bool(intent.get("detected"))
    probe_planned = "no"
    probe_endpoint = ""
    use_proxy = False
    include_headers = False
    probe_type = "auto"
    if should_offer_probe and interactive_prompt_yes_no("This script appears network-dependent. Probe first?", default=bool(intent.get("confidence") == "high" and not quick_mode)):
        endpoint_type = interactive_prompt_select(
            "Endpoint type:",
            [("api", "API"), ("m3u8", "M3U8")],
            default_key="m3u8" if effective_kind == "m3u8" else "api",
        )
        probe_endpoint = interactive_prompt_text("Endpoint / URL:", str(intent.get("endpoint") or ""))
        use_proxy = interactive_prompt_yes_no(
            "Use proxy settings for this probe?",
            default=bool(session.get("http_proxy") or session.get("https_proxy")),
        )
        include_headers = interactive_prompt_yes_no("Include headers/auth from the request context when relevant?", default=False)
        probe_type = "m3u8_summary" if endpoint_type == "m3u8" else "json_summary"
        probe_planned = f"yes ({endpoint_type} -> {probe_endpoint})"
    validation_choice, custom_validation_command, validation_display = interactive_new_script_validation_choice(quick_mode, resolved_output)
    allow_repair = True
    allow_publish_offer = True
    advanced_notes: list[str] = []
    if not quick_mode and interactive_prompt_yes_no("Show advanced options?", default=False):
        allow_repair = interactive_prompt_yes_no("Allow automatic repair if generation validation fails?", default=True)
        allow_publish_offer = interactive_prompt_yes_no("Offer publish after a successful generation?", default=True)
        if interactive_prompt_yes_no("Skip validation after generation?", default=False):
            validation_choice = "skip"
            validation_display = "skip validation"
            custom_validation_command = ""
        if not pattern_repo_cli and pattern_source != "specific" and interactive_prompt_yes_no("Override pattern repo explicitly?", default=False):
            override_repo = interactive_prompt_text("Pattern repo name or path:")
            if override_repo:
                pattern_source = override_repo
                pattern_repo_cli = override_repo
                pattern_args = ["--pattern-repo", override_repo]
        advanced_notes.append(f"repair_on_failure: {format_bool(allow_repair)}")
    selection, preview_plan = interactive_plan_new_script_generation(
        repo_path,
        purpose,
        resolved_output,
        pattern_repo_cli=pattern_repo_cli,
        probe_planned=probe_planned,
    )
    likely_patterns = [str(item.get("pattern_type") or "") for item in list(selection.get("applied") or [])[:4] if item.get("pattern_type")]
    expected_structure = list(preview_plan.get("proposed_script_structure") or [])
    generate_command = build_interactive_common_args(session, repo=repo, include_proxy=use_proxy, include_output=True)
    generate_command.extend(pattern_args)
    generate_command.extend(["--new-script", output_display, "--new-script-purpose", purpose])
    if validation_choice != "auto":
        generate_command.extend(["--new-script-validation-mode", validation_choice])
    if custom_validation_command:
        generate_command.extend(["--test-cmd", custom_validation_command])
    if probe_endpoint:
        generate_command.extend(["--probe-url", probe_endpoint, "--probe-type", probe_type])
    repair_command = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False)
    repair_command.extend(["--script", output_display, "--no-finalize"])
    repair_command.extend(pattern_args)
    if validation_choice == "custom" and custom_validation_command:
        repair_command.extend(["--test-cmd", custom_validation_command])
    elif validation_choice == "cli_help":
        repair_command.extend(["--test-cmd", f"python {output_display} --help"])
    elif validation_choice == "syntax":
        repair_command.extend(["--test-cmd", f"python -m py_compile {output_display}"])
    publish_command = {
        "label": "publish new script",
        "compact_preview": "Publish the new script through the guarded finalizer",
        "preview_command": "fixpublish",
        "run_command": ["./scripts/fixpublish.sh"],
        "cwd": str(repo_path),
    }
    notes = [
        f"generation_plan: {preview_plan.get('task_purpose') or purpose}",
        "patterns_likely_to_be_applied: " + (str(likely_patterns) if likely_patterns else "[]"),
        "expected_script_structure: " + (str(expected_structure) if expected_structure else "['main entrypoint']"),
        f"planned_validation_method: {validation_display}",
    ]
    if probe_planned != "no":
        notes.append(f"probe_plan: {probe_planned}")
    elif intent.get("detected"):
        notes.append(f"network_detection: {intent.get('reason')}")
    notes.extend(advanced_notes)
    return {
        "workflow": "Create a new script",
        "description": "Use this to turn a script idea into generated code, validation, and an optional safe publish handoff.",
        "inputs": {
            "repo": repo,
            "purpose": purpose,
            "output_path": output_display,
            "script_domain": domain_choice,
            "pattern_source": pattern_source,
            "probe_planned": probe_planned,
            "validation_plan": validation_display,
            "workflow_mode": workflow_mode,
        },
        "notes": notes,
        "commands": [
            {
                "label": "create script",
                "compact_preview": "Generate the new script and run the selected validation plan",
                "args": generate_command,
            }
        ],
        "result_renderer": render_interactive_new_script_result,
        "failure_handler": interactive_new_script_failure_handler,
        "post_success_handler": interactive_new_script_post_success_handler if allow_publish_offer else None,
        "result_context": {
            "probe_planned": probe_planned != "no",
            "repair_allowed": allow_repair and validation_choice != "skip",
            "repair_attempted": False,
            "repair_result": "not_needed",
            "publish_attempted": False,
            "repair_command": {
                "label": "repair generated script",
                "compact_preview": "Repair and revalidate the generated script",
                "args": repair_command,
            },
            "publish_command": publish_command,
        },
    }


def interactive_publish_preflight_state(
    repo: Path,
    *,
    auto_stage_safe_files: bool,
    auto_remediate_blockers: bool,
    run_validation_if_needed: str,
    force_publish: bool,
) -> dict:
    branch = current_git_branch(repo)
    changes = classify_publishable_changes(repo)
    working_tree = classify_publish_working_tree(repo)
    audit = normalize_publish_working_tree_audit(
        repo,
        working_tree,
        list(changes.get("meaningful_paths") or []),
        publish_current_mode=True,
    )
    decisions, summary, remaining_unstaged, overall_reason = build_publish_file_decisions(
        list(audit.get("entries") or []),
        expected_paths=list(changes.get("meaningful_paths") or []),
        staged_paths=audit.get("staged_paths") or [],
        remaining_paths=audit.get("remaining_paths") or [],
    )
    decision_sets = summarize_publish_decision_sets(decisions, remaining_unstaged)
    unresolved_for_analysis = [
        item
        for item in remaining_unstaged
        if item.get("path") in set(decision_sets.get("safe_stage_candidate_paths") or [])
        or item.get("path") in {str(entry.get("path") or "") for entry in (decision_sets.get("true_blockers") or [])}
    ]
    blocker_policy = load_publish_blocker_policy(repo, auto_remediate=auto_remediate_blockers)
    blocked_file_analysis = [
        {**item, **classify_publish_blocker_remediation(repo, item, blocker_policy)}
        for item in analyze_publish_blockers(repo, unresolved_for_analysis)
    ]
    blocked_analysis_summary = summarize_publish_block_analysis(blocked_file_analysis)
    blocked_analysis_summary["safe_staged_paths"] = list(decision_sets.get("safe_staged_paths") or [])
    blocked_analysis_summary["ignored_nonblocking_paths"] = list(decision_sets.get("ignored_nonblocking_paths") or [])
    blocked_analysis_summary["true_blockers"] = list(decision_sets.get("true_blockers") or [])
    blocked_analysis_summary["blocker_count"] = int(decision_sets.get("blocker_count") or 0)
    blocked_analysis_summary["publishable_ready"] = bool(decision_sets.get("publishable_ready"))
    validation_state = resolve_publish_validation_state(repo)
    remembered_validation_command = latest_repo_validation_command(repo)
    safe_paths = list(decision_sets.get("safe_stage_candidate_paths") or [])
    blocked_paths = [str(item.get("path") or "") for item in (decision_sets.get("true_blockers") or [])]
    auto_resolvable_blockers = [
        str(item.get("path") or "")
        for item in blocked_file_analysis
        if str(item.get("remediation_class") or "") == "auto_resolvable_safe"
    ]
    unresolved_blockers_after_remediation = [
        str(item.get("path") or "")
        for item in blocked_file_analysis
        if str(item.get("remediation_class") or "") != "auto_resolvable_safe"
    ]
    revalidation_planned = bool(
        run_validation_if_needed == "auto"
        and str(validation_state.get("validation_result") or "blocked") != "success"
        and remembered_validation_command
    )
    would_block = False
    would_block_reason = ""
    if not changes.get("meaningful_changes_detected"):
        would_block = False
        would_block_reason = "no meaningful changes detected"
    elif not auto_stage_safe_files and audit.get("remaining_paths"):
        would_block = True
        would_block_reason = (
            "safe publishable files still need manual staging because automatic staging is disabled"
            if safe_paths and not blocked_paths
            else "publishable or ambiguous files remain unstaged and automatic staging is disabled"
        )
    elif auto_stage_safe_files and blocked_paths and unresolved_blockers_after_remediation:
        would_block = True
        would_block_reason = "one or more unsafe or ambiguous files would still require manual review"
    elif (
        str(validation_state.get("validation_result") or "blocked") != "success"
        and not force_publish
        and run_validation_if_needed == "skip"
    ):
        would_block = True
        would_block_reason = "validation does not match the current repo state and revalidation was skipped"
    elif (
        str(validation_state.get("validation_result") or "blocked") != "success"
        and not force_publish
        and run_validation_if_needed == "auto"
        and not remembered_validation_command
    ):
        would_block = True
        would_block_reason = "validation is stale or missing and no remembered validation command is available for revalidation"
    staging_plan = "all publishable changes already staged"
    if audit.get("remaining_paths"):
        if auto_stage_safe_files:
            staging_plan = (
                f"auto-stage {len(safe_paths)} safe path(s)"
                + (f"; auto-resolve {len(auto_resolvable_blockers)} safe blocker(s)" if auto_resolvable_blockers and auto_remediate_blockers else "")
                + (f"; manual review required for {len(unresolved_blockers_after_remediation)} unsafe path(s)" if unresolved_blockers_after_remediation else "")
            )
        else:
            staging_plan = "manual staging required for remaining publishable files"
    return {
        "repo": str(repo),
        "branch": branch,
        "changes": changes,
        "working_tree": working_tree,
        "audit": audit,
        "file_decisions": decisions,
        "staging_summary": summary,
        "remaining_unstaged": remaining_unstaged,
        "safe_staged_paths": decision_sets.get("safe_staged_paths") or [],
        "ignored_nonblocking_paths": decision_sets.get("ignored_nonblocking_paths") or [],
        "safe_stage_candidate_paths": decision_sets.get("safe_stage_candidate_paths") or [],
        "true_blockers": decision_sets.get("true_blockers") or [],
        "blocker_count": int(decision_sets.get("blocker_count") or 0),
        "publishable_ready": bool(decision_sets.get("publishable_ready")),
        "auto_remediate_blockers": auto_remediate_blockers,
        "auto_resolvable_blockers": auto_resolvable_blockers,
        "unresolved_blockers_after_remediation": unresolved_blockers_after_remediation,
        "blocked_file_analysis": blocked_file_analysis,
        "blocked_analysis_summary": blocked_analysis_summary,
        "staging_decision_reason": overall_reason,
        "validation_state": validation_state,
        "validation_record_exists": bool(validation_state.get("last_validated_commit")),
        "validation_commit_match": bool(validation_state.get("validation_commit_match")),
        "revalidation_planned": revalidation_planned,
        "remembered_validation_command": remembered_validation_command,
        "would_block": would_block,
        "would_block_reason": would_block_reason,
        "staging_plan": staging_plan,
        "preflight": build_publish_preflight(repo, branch),
    }


def render_interactive_publish_result(action: dict, executed: list[dict]) -> None:
    combined_output = "\n".join(str(item.get("output") or "") for item in executed)
    preflight = action.get("result_context", {}).get("preflight", {}) or {}
    validation_result = interactive_extract_output_value(combined_output, "validation_result") or "blocked"
    publish_triggered = interactive_extract_output_value(combined_output, "publish_triggered") or "false"
    publish_result = (
        interactive_extract_output_value(combined_output, "publish_result")
        or interactive_extract_output_value(combined_output, "final_status")
        or interactive_extract_output_value(combined_output, "final_workflow_result")
        or "failed"
    )
    pr_url = interactive_extract_output_value(combined_output, "pr_url") or "(none)"
    branch_used = interactive_extract_output_value(combined_output, "branch") or str(action.get("result_context", {}).get("preflight", {}).get("branch") or "(none)")
    mergeability = interactive_extract_output_value(combined_output, "pr_mergeable_final") or interactive_extract_output_value(combined_output, "pr_mergeable") or "unknown"
    blocked_reason = (
        interactive_extract_output_value(combined_output, "blocked_reason")
        or interactive_extract_output_value(combined_output, "publish_detail_reason")
        or interactive_extract_output_value(combined_output, "staging_reason")
        or interactive_extract_output_value(combined_output, "reason")
    )
    blocker_remediation_result = interactive_extract_output_value(combined_output, "blocker_remediation_result") or "not_needed"
    auto_removed_paths = interactive_extract_output_value(combined_output, "auto_removed_paths") or "[]"
    auto_revalidation_result = interactive_extract_output_value(combined_output, "auto_revalidation_result")
    plain_english = "The publish workflow completed."
    if publish_result == "success" and blocker_remediation_result == "success" and auto_removed_paths not in {"", "[]"}:
        plain_english = "Removed safe temporary artifact blockers and continued publish."
    elif publish_result == "success":
        plain_english = "Changes were validated and published successfully."
    elif publish_result == "blocked" and "unstaged" in blocked_reason.lower():
        plain_english = "Publish blocked due to unstaged files."
    elif auto_revalidation_result in {"success", "failed", "blocked"} and preflight.get("validation_commit_match") is False:
        plain_english = "Publish required revalidation due to commit mismatch."
    elif publish_result == "noop":
        plain_english = "No meaningful changes detected; nothing was published."
    elif publish_result == "blocked":
        plain_english = "Publish blocked before changes could be pushed."
    next_step = ""
    if publish_result == "blocked":
        blocked_summary = preflight.get("blocked_analysis_summary") or {}
        next_step = str(blocked_summary.get("primary_next_step") or "")
        if not next_step and blocked_reason:
            next_step = "Review the blocker above, fix it, then rerun publish."
    interactive_print_result(
        action.get("workflow") or "Publish current repo state",
        success=publish_result == "success",
        fields=[
            ("validation_result", validation_result),
            ("publish_triggered", publish_triggered),
            ("publish_result", publish_result),
            ("blocker_remediation_result", blocker_remediation_result),
            ("auto_removed_paths", auto_removed_paths),
            ("pr_url", pr_url),
            ("branch_used", branch_used),
            ("mergeability_result", mergeability),
        ],
        blocked_reason=blocked_reason if publish_result != "success" else "",
        next_step=next_step,
        what_happened=plain_english,
    )
    if publish_result == "blocked" and preflight.get("blocked_file_analysis"):
        print("")
        print_publish_block_analysis(
            preflight.get("blocked_file_analysis") or [],
            preflight.get("blocked_analysis_summary") or {},
        )


def interactive_select_training_repo(session: dict) -> tuple[str, Path, list[str]]:
    choice = interactive_prompt_select(
        "Training repo:",
        [
            ("default", "Auto/default"),
            ("existing", "Choose existing repo"),
            ("create", "Create new repo"),
        ],
        default_key="default",
    )
    if choice == "default":
        return "default", default_pattern_repo_path(), ["--pattern-repo", "default"]
    selected = interactive_prompt_text("Pattern repo name or path:")
    target_path = normalize_pattern_repo_path(selected) if selected else default_pattern_repo_path()
    return selected or choice, target_path, ["--pattern-repo", str(target_path)]


def confidence_label_from_score(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.7:
        return "medium"
    return "low"


def interactive_prepare_training_import(
    *,
    source_value: str,
    source_type: str,
    target_repo: Path,
    requested_trust: str,
    sanitize_before_import: bool,
    validate_before_promote: bool,
    allow_auto_fix: bool,
    pattern_tags: list[str],
    pattern_type_hint: str,
) -> dict:
    fetched = fetch_pattern_source(source_value, Path.cwd())
    result = {
        "ok": bool(fetched.get("ok")),
        "source_type": fetched.get("source_type", source_type),
        "source_origin": str(fetched.get("source_origin") or source_value),
        "acquisition_method": str(fetched.get("acquisition_method") or "direct"),
        "proxy_used": bool(fetched.get("proxy_used")),
        "blocked_reason": str(fetched.get("blocked_reason") or ""),
        "sanitized_changed": False,
        "sanitization_applied": False,
        "validation_result": "not_run",
        "validation_passed": False,
        "validation_command": "",
        "repair_attempted": False,
        "repair_result": "not_needed",
        "repair_output": "",
        "pattern_type": pattern_type_hint or "",
        "applicability_context": [],
        "confidence_score": 0.0,
        "confidence_level": "low",
        "recommended_trust": requested_trust,
        "safe_for_trusted": False,
        "warnings": [],
    }
    if not fetched.get("ok"):
        return result
    raw_content = str(fetched.get("content") or "")
    sanitized_content = raw_content
    changed = False
    if sanitize_before_import:
        sanitized_content, changed = sanitize_pattern_script_content(raw_content)
    result["sanitized_changed"] = changed
    result["sanitization_applied"] = sanitize_before_import
    if changed:
        result["warnings"].append("sanitization removed or redacted sensitive content")
    with tempfile.TemporaryDirectory(prefix="lfa-import-preview-") as tmpdir:
        preview_repo = Path(tmpdir)
        candidate_path = preview_repo / (Path(parse_pattern_import_source(source_value).get("display_name") or "script.py").name or "script.py")
        candidate_path.write_text(sanitized_content)
        extracted = extract_script_patterns_with_metadata(
            preview_repo,
            candidate_path,
            {
                "source_origin": result["source_origin"],
                "source_type": result["source_type"],
                "tags": list(pattern_tags or []),
                "trust_level": requested_trust,
            },
        )
        if extracted:
            top = max(extracted, key=lambda item: float(item.get("confidence", 0) or 0))
            result["pattern_type"] = pattern_type_hint or str(top.get("pattern_type") or top.get("family") or "")
            result["applicability_context"] = list(top.get("applicability_context") or [])
            result["confidence_score"] = float(top.get("confidence", 0) or 0)
            result["confidence_level"] = confidence_label_from_score(result["confidence_score"])
        else:
            result["pattern_type"] = pattern_type_hint or "unknown"
            result["confidence_level"] = "low"
            result["warnings"].append("pattern classification confidence is low")
        if validate_before_promote:
            validation = run_candidate_validation(preview_repo, candidate_path)
            result["validation_passed"] = bool(validation.get("passed"))
            result["validation_result"] = "success" if validation.get("passed") else "blocked"
            result["validation_command"] = str(validation.get("validation_command") or "")
            limited = bool(validation.get("limited_validation"))
            if not validation.get("passed") and allow_auto_fix:
                repair = repair_training_candidate(preview_repo, candidate_path)
                result["repair_attempted"] = True
                result["repair_output"] = str(repair.get("output") or "")
                if repair.get("ok"):
                    validation = run_candidate_validation(preview_repo, candidate_path)
                    result["validation_passed"] = bool(validation.get("passed"))
                    result["validation_result"] = "success" if validation.get("passed") else "blocked"
                    result["validation_command"] = str(validation.get("validation_command") or "")
                    limited = bool(validation.get("limited_validation"))
                    result["repair_result"] = "success" if validation.get("passed") else "failed"
                else:
                    result["repair_result"] = "failed"
            elif not validation.get("passed"):
                result["repair_result"] = "skipped"
            result["safe_for_trusted"] = bool(result["validation_passed"]) and not limited and result["confidence_level"] != "low"
        else:
            result["validation_result"] = "skipped"
            result["warnings"].append("validation was skipped before promotion")
        if result["confidence_level"] == "low":
            result["recommended_trust"] = "experimental"
            result["warnings"].append("low confidence suggests experimental trust")
        elif not result["safe_for_trusted"] and requested_trust == "trusted":
            result["recommended_trust"] = "experimental"
    return result


def render_interactive_import_training_result(action: dict, executed: list[dict]) -> None:
    combined_output = "\n".join(str(item.get("output") or "") for item in executed)
    result_context = action.get("result_context", {}) or {}
    trust_level = str(result_context.get("final_trust") or action.get("inputs", {}).get("trust_level") or "trusted")
    import_success = next((int(item.get("returncode", 0)) == 0 for item in reversed(executed)), False)
    target_repo = interactive_extract_output_value(combined_output, "training_repo") or interactive_extract_output_value(combined_output, "pattern_repo") or str(action.get("inputs", {}).get("target_repo") or "")
    learned_delta = interactive_extract_output_value(combined_output, "learned_pattern_delta") or "0"
    warnings = list(result_context.get("warnings") or [])
    validated = "validated=true" in combined_output or "validated=True" in combined_output
    repaired = "repaired=true" in combined_output or "repaired=True" in combined_output
    promoted = "promoted_to_training=true" in combined_output or "promoted_to_training=True" in combined_output
    blocked_reason = interactive_extract_output_value(combined_output, "blocked_reason")
    validation_result = str(result_context.get("validation_result") or action.get("inputs", {}).get("validation_result") or "not_run")
    repair_result = str(result_context.get("repair_result") or action.get("inputs", {}).get("repair_result") or "not_needed")
    plain_english = "Import did not complete."
    if import_success and promoted and repaired:
        plain_english = "The script required repair before being accepted into training."
    elif import_success and promoted and trust_level == "trusted":
        plain_english = "The script was sanitized, validated, and added as a trusted pattern."
    elif import_success and trust_level == "experimental" and validation_result != "success":
        plain_english = "The script failed validation and was added as experimental."
    elif import_success and trust_level == "experimental":
        plain_english = "The script was sanitized and added as an experimental pattern."
    elif not import_success:
        plain_english = "Import was blocked due to unsafe content."
    interactive_print_result(
        action.get("workflow") or "Import a script into training",
        success=import_success,
        fields=[
            ("import_success", format_bool(import_success)),
            ("target_repo_path", target_repo or "(none)"),
            ("trust_level_applied", trust_level),
            ("learned_pattern_count_change", learned_delta),
            ("validation_result", validation_result),
            ("repair_result", repair_result),
            ("validated", format_bool(validated)),
            ("repaired", format_bool(repaired)),
        ] + ([("warnings", str(warnings))] if warnings else []),
        blocked_reason=blocked_reason if not import_success else "",
        next_step="Review the blocked reason, sanitize the source further, or retry with experimental trust." if not import_success else "",
        what_happened=plain_english,
    )


def execute_interactive_action(action: dict) -> int:
    commands = action.get("commands") or []
    executed: list[dict] = []

    def run_item(item: dict) -> dict:
        label = str(item.get("label") or "command")
        args = list(item.get("args") or [])
        run_command = list(item.get("run_command") or [])
        print(f"\n=== RUNNING: {label} ===")
        result = run_interactive_backend_command(args) if not run_command else {
            "returncode": 0,
            "output": "",
        }
        if run_command:
            completed = subprocess.Popen(
                run_command,
                cwd=str(item.get("cwd") or Path.cwd()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            lines: list[str] = []
            assert completed.stdout is not None
            for line in completed.stdout:
                print(line, end="")
                lines.append(line)
            result = {"returncode": completed.wait(), "output": "".join(lines)}
        return {"label": label, **result}

    for item in commands:
        result = run_item(item)
        executed.append(result)
        if result["returncode"] != 0:
            failure_handler = action.get("failure_handler")
            if callable(failure_handler):
                failure_result = failure_handler(action, executed, result, run_item)
                if isinstance(failure_result, dict):
                    if failure_result.get("executed"):
                        executed.extend(list(failure_result.get("executed") or []))
                    if failure_result.get("recovered"):
                        continue
                    if "returncode" in failure_result:
                        result = {"label": result.get("label", "command"), "returncode": int(failure_result.get("returncode", result["returncode"])), "output": result.get("output", "")}
            renderer = action.get("result_renderer")
            if callable(renderer):
                renderer(action, executed)
            print(f"interactive_step_failed: {result['label']} exit_code={result['returncode']}")
            return int(result["returncode"])
    post_success_handler = action.get("post_success_handler")
    if callable(post_success_handler):
        post_success_result = post_success_handler(action, executed, run_item)
        if isinstance(post_success_result, dict) and post_success_result.get("executed"):
            executed.extend(list(post_success_result.get("executed") or []))
        if isinstance(post_success_result, dict) and int(post_success_result.get("returncode", 0)) != 0:
            renderer = action.get("result_renderer")
            if callable(renderer):
                renderer(action, executed)
            return int(post_success_result.get("returncode", 0))
    renderer = action.get("result_renderer")
    if callable(renderer):
        renderer(action, executed)
    print("interactive_action_result: success")
    return 0


def interactive_select_pattern_repo(session: dict, prompt_label: str = "Pattern repo mode:") -> tuple[str, list[str]]:
    choice = interactive_prompt_select(
        prompt_label,
        [
            ("default", "Default / auto"),
            ("none", "Disable learned patterns"),
            ("specific", "Specific repo or path"),
        ],
        default_key="default",
    )
    if choice == "none":
        return "none", ["--pattern-repo", "none"]
    if choice == "specific":
        value = interactive_prompt_text("Pattern repo name or path:")
        return value, ["--pattern-repo", value] if value else []
    return "default", []


def interactive_fix_validate_action(session: dict) -> dict:
    quick_mode = interactive_workflow_mode(session) == "quick"
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    repo_path = Path(repo).expanduser()
    script = interactive_prompt_text("Script path:")
    script_path = Path(script).expanduser()
    mode = "fix_and_validate" if quick_mode else interactive_prompt_select(
        "What should this run do?",
        [("fix_and_validate", "Fix and validate (recommended)"), ("validate_only", "Validate only")],
        default_key="fix_and_validate",
    )
    default_validation_command = latest_repo_validation_command(repo_path) if repo_path.exists() else ""
    validation_choice = "auto_detect" if quick_mode else interactive_prompt_select(
        "How should this be validated?",
        [("auto_detect", "Auto-detect (recommended)"), ("use_default", "Use default"), ("custom", "Enter custom command")],
        default_key="auto_detect",
    )
    selected_validation_command = ""
    validation_note = ""
    if validation_choice == "use_default":
        if default_validation_command:
            selected_validation_command = default_validation_command
            validation_note = f"Using remembered validation command: {default_validation_command}"
        else:
            validation_choice = "auto_detect"
            validation_note = "No remembered validation command exists for this repo, so auto-detect will be used."
    if validation_choice == "custom":
        selected_validation_command = interactive_prompt_text("Custom validation command:")
    pattern_choice = "auto" if quick_mode else interactive_prompt_select(
        "Which pattern repo should be used?",
        [
            ("auto", "Auto (recommended)"),
            ("default", "Default pattern repo"),
            ("none", "None"),
            ("specific", "Specific repo or path"),
        ],
        default_key="auto",
    )
    pattern_mode = pattern_choice
    pattern_args: list[str] = []
    if pattern_choice == "default":
        pattern_args = ["--pattern-repo", "default"]
    elif pattern_choice == "none":
        pattern_args = ["--pattern-repo", "none"]
    elif pattern_choice == "specific":
        specific_repo = interactive_prompt_text("Pattern repo name or path:")
        pattern_mode = specific_repo or "specific"
        if specific_repo:
            pattern_args = ["--pattern-repo", specific_repo]
    features = extract_script_features(script_path) if script_path.exists() and script_path.is_file() else {
        "url_literals": [],
        "mentions_m3u8": False,
        "uses_network_client": False,
        "text": "",
    }
    network_dependency = classify_network_dependency(features)
    probe_recommendations = recommend_script_probe_targets(features)
    probe_command: list[str] | None = None
    probe_summary = "no"
    if network_dependency.get("detected") and not quick_mode:
        if interactive_prompt_yes_no("This script looks network-dependent. Probe endpoint now?", default=False):
            probe_kind = interactive_prompt_select(
                "Probe type:",
                [("api", "API"), ("m3u8", "M3U8")],
                default_key="m3u8" if features.get("mentions_m3u8") else "api",
            )
            obvious_endpoint = str((probe_recommendations[0].get("endpoint") if probe_recommendations else "") or "")
            endpoint = interactive_prompt_text("Probe endpoint:", obvious_endpoint)
            use_proxy = interactive_prompt_yes_no(
                "Use proxy settings for this probe?",
                default=bool(session.get("http_proxy") or session.get("https_proxy")),
            )
            include_headers = bool(extract_probe_header_candidates(features.get("text") or "")) and interactive_prompt_yes_no(
                "Include headers detected from the script?",
                default=False,
            )
            probe_command = build_interactive_common_args(session, repo=repo, include_proxy=use_proxy, include_output=True)
            probe_command.extend(["--probe-url", endpoint, "--probe-type", "m3u8_summary" if probe_kind == "m3u8" else "json_summary"])
            if include_headers:
                for name, value in extract_probe_header_candidates(features.get("text") or "").items():
                    probe_command.extend(["--probe-header", f"{name}: {value}"])
            probe_summary = f"yes ({probe_kind} -> {endpoint})"
    fix_command = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False)
    fix_command.extend(["--script", script])
    fix_command.extend(pattern_args)
    advanced_notes: list[str] = []
    if selected_validation_command:
        fix_command.extend(["--test-cmd", selected_validation_command])
    chosen_validation_display = selected_validation_command or ("auto-detect" if validation_choice == "auto_detect" else "default")
    advanced_options = False
    if not quick_mode and interactive_prompt_yes_no("Show advanced options?", default=False):
        advanced_options = True
        mode_choice = interactive_prompt_select(
            "Which engine mode should be used?",
            [("quick", "Quick"), ("safe", "Safe (recommended)"), ("deep", "Deep"), ("benchmark", "Benchmark")],
            default_key="safe",
        )
        fix_command.extend(["--mode", mode_choice])
        if interactive_prompt_yes_no("Disable auto-stage during finalization?", default=False):
            fix_command.append("--no-auto-stage")
        if interactive_prompt_yes_no("Disable auto-revalidation during publish/finalization?", default=False):
            fix_command.append("--no-auto-revalidate")
        if interactive_prompt_yes_no("Disable auto conflict resolution after sync?", default=False):
            fix_command.append("--no-auto-conflict-resolution-after-sync")
        if interactive_prompt_yes_no("Require manual conflict handling immediately?", default=False):
            fix_command.append("--no-auto-merge-conflicts")
        if pattern_choice != "specific" and interactive_prompt_yes_no("Override pattern repo explicitly?", default=False):
            override_repo = interactive_prompt_text("Pattern repo name or path:")
            if override_repo:
                pattern_mode = override_repo
                pattern_args = ["--pattern-repo", override_repo]
                fix_command = [item for item in fix_command if item != "--pattern-repo" and item != "default" and item != "none"]
                if "--pattern-repo" in fix_command:
                    idx = fix_command.index("--pattern-repo")
                    del fix_command[idx:idx + 2]
                fix_command.extend(pattern_args)
        if interactive_prompt_yes_no("Strict/manual behavior: skip finalization after a successful fix?", default=False):
            fix_command.append("--no-finalize")
    commands = []
    if probe_command:
        commands.append({"label": "probe endpoint", "args": probe_command, "compact_preview": "Run bounded live probe"})
    if mode == "validate_only":
        validate_command = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False)
        validate_command.extend(["--script", script, "--script-validate-only"])
        validate_command.extend(pattern_args)
        if selected_validation_command:
            validate_command.extend(["--test-cmd", selected_validation_command])
        commands.append({"label": "validate script", "args": validate_command, "compact_preview": "Validate the script"})
    else:
        commands.append({"label": "fix/validate", "args": fix_command, "compact_preview": "Fix and validate the script"})
    notes: list[str] = []
    if validation_note:
        notes.append(validation_note)
    if network_dependency.get("detected"):
        notes.append(
            "Network-dependent signals detected: "
            + str(network_dependency.get("reason") or "live endpoint behavior may matter")
        )
    return {
        "workflow": "Fix or validate a script",
        "description": "Use this when you want the agent to repair or validate one script through the existing backend.",
        "scaffolded": False,
        "inputs": {
            "repo": repo,
            "script_path": script,
            "mode": mode,
            "validation_choice": chosen_validation_display,
            "pattern_source": pattern_mode,
            "probe_planned": probe_summary,
            "advanced_options": advanced_options,
            "workflow_mode": "quick" if quick_mode else "guided",
        },
        "notes": notes,
        "commands": commands,
        "result_context": {
            "probe_planned": bool(probe_command),
            "validation_command_used": chosen_validation_display,
        },
        "result_renderer": render_interactive_fix_validate_result,
    }


def interactive_publish_current_action(session: dict) -> dict:
    quick_mode = interactive_workflow_mode(session) == "quick"
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    repo_path = Path(repo).expanduser()
    publish_mode = "normal" if quick_mode else interactive_prompt_select(
        "How should publish run?",
        [("normal", "Normal (recommended)"), ("force", "Force publish")],
        default_key="normal",
    )
    auto_stage_safe_files = True if quick_mode else interactive_prompt_select(
        "Auto-stage safe files?",
        [("yes", "Yes (recommended)"), ("no", "No - manual staging")],
        default_key="yes",
    ) == "yes"
    auto_remediate_blockers = True if quick_mode else interactive_prompt_yes_no(
        "Auto-resolve safe blockers such as temporary artifacts?",
        default=True,
    )
    run_validation_if_needed = "auto" if quick_mode else interactive_prompt_select(
        "How should validation be handled?",
        [("auto", "Auto (recommended)"), ("skip", "Skip")],
        default_key="auto",
    )
    explain_staging = False
    no_auto_conflict_resolution = False
    if not quick_mode and interactive_prompt_yes_no("Show advanced options?", default=False):
        explain_staging = interactive_prompt_yes_no("Print detailed staging explanation?", default=False)
        no_auto_conflict_resolution = interactive_prompt_yes_no("Disable auto conflict resolution after sync?", default=False)
    preflight = interactive_publish_preflight_state(
        repo_path,
        auto_stage_safe_files=auto_stage_safe_files,
        auto_remediate_blockers=auto_remediate_blockers,
        run_validation_if_needed=run_validation_if_needed,
        force_publish=publish_mode == "force",
    )
    preview_args = ["./scripts/fixpublish.sh", "--repo", repo]
    wrapper_args: list[str] = ["--repo", repo]
    if publish_mode == "force":
        preview_args.append("--force-publish")
        wrapper_args.append("--force-publish")
    if not auto_stage_safe_files:
        preview_args.append("--no-auto-stage")
        wrapper_args.append("--no-auto-stage")
    if not auto_remediate_blockers:
        preview_args.append("--no-auto-remediate-blockers")
        wrapper_args.append("--no-auto-remediate-blockers")
    if run_validation_if_needed == "skip":
        preview_args.append("--no-auto-revalidate")
        wrapper_args.append("--no-auto-revalidate")
    if explain_staging:
        preview_args.append("--explain-staging")
        wrapper_args.append("--explain-staging")
    if no_auto_conflict_resolution:
        preview_args.append("--no-auto-conflict-resolution-after-sync")
        wrapper_args.append("--no-auto-conflict-resolution-after-sync")
    launcher_path = str((Path(__file__).resolve().parent / "scripts" / "fixpublish.sh").resolve())
    return {
        "workflow": "Publish current repo state",
        "description": "Use this when you want to publish the current working tree through the guarded publish-current path.",
        "scaffolded": False,
        "inputs": {
            "repo": repo,
            "publish_mode": publish_mode,
            "auto_remediate_safe_blockers": auto_remediate_blockers,
            "files_to_be_published": preflight["changes"].get("meaningful_paths") or [],
            "changed_files": preflight["changes"].get("meaningful_paths") or [],
            "staged_paths": preflight["working_tree"].get("staged_paths") or [],
            "unstaged_paths": sorted(set(preflight["working_tree"].get("unstaged_paths") or []) | set(preflight["working_tree"].get("untracked_paths") or [])),
            "validation_record_exists": preflight["validation_record_exists"],
            "validation_commit_match": preflight["validation_commit_match"],
            "validation_status": preflight["validation_state"].get("validation_result") or "blocked",
            "revalidation_will_run": preflight["revalidation_planned"],
            "publish_would_block": preflight["would_block"],
            "publish_block_reason": preflight["would_block_reason"] or "(none)",
            "staging_plan": preflight["staging_plan"],
            "workflow_mode": "quick" if quick_mode else "guided",
        },
        "notes": [
            f"staging_decision_reason: {preflight['staging_decision_reason']}",
            f"safe_staged_paths: {preflight.get('safe_staged_paths') or []}",
            f"ignored_nonblocking_paths: {preflight.get('ignored_nonblocking_paths') or []}",
            f"true_blockers: {preflight.get('true_blockers') or []}",
            f"auto_resolvable_blockers: {preflight.get('auto_resolvable_blockers') or []}",
            (
                "remaining_unstaged: "
                + ", ".join(
                    f"{item.get('path')} ({item.get('reason')})"
                    for item in (preflight["remaining_unstaged"] or [])
                )
            ) if preflight["remaining_unstaged"] else "remaining_unstaged: none",
            (
                "next_step_primary: "
                + str((preflight.get("blocked_analysis_summary") or {}).get("primary_next_step") or "(none)")
            ),
        ],
        "commands": [
            {
                "label": "publish current repo state",
                "args": [],
                "compact_preview": f"Run fixpublish ({'auto-stage on' if auto_stage_safe_files else 'manual staging'})",
                "preview_command": interactive_preview_shell_command(preview_args),
                "run_command": [launcher_path, *wrapper_args],
            }
        ],
        "result_context": {"preflight": preflight},
        "result_renderer": render_interactive_publish_result,
    }


def interactive_publish_validated_action(session: dict) -> dict:
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    default_behavior = interactive_prompt_yes_no("Use default finalizer behavior?", default=True)
    ensure_args = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False) + ["--ensure-validation-record"]
    publish_args = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False) + ["--publish-only"]
    if default_behavior:
        publish_args.append("--publish-pr")
    return {
        "workflow": "Publish last validated run",
        "description": "Use this when a validated state already exists and you want the canonical finalizer flow.",
        "scaffolded": True,
        "inputs": {
            "repo": repo,
            "use_default_finalizer_behavior": default_behavior,
        },
        "notes": [
            "This follows the canonical finalizer path: ensure a validation record, then publish.",
            interactive_standard_scaffold_note(),
        ],
        "commands": [
            {"label": "ensure validation record", "args": ensure_args},
            {"label": "publish validated state", "args": publish_args},
        ],
    }


def render_interactive_config_result(action: dict, executed: list[dict]) -> None:
    combined_output = "\n".join(str(item.get("output") or "") for item in executed)
    config_type = interactive_extract_output_value(combined_output, "config_type") or str(action.get("inputs", {}).get("config_type") or "unknown")
    task = interactive_extract_output_value(combined_output, "task") or str(action.get("inputs", {}).get("task") or "")
    validation_result = interactive_extract_output_value(combined_output, "validation_result") or "blocked"
    changes_made = interactive_extract_output_value(combined_output, "changes_made") or "false"
    confidence = interactive_extract_output_value(combined_output, "confidence") or str(action.get("inputs", {}).get("confidence") or "low")
    blocked_reason = interactive_extract_output_value(combined_output, "blocked_reason")
    validation_command = interactive_extract_output_value(combined_output, "validation_command") or str(action.get("inputs", {}).get("validation_command") or "")
    pattern_source_used = interactive_extract_output_value(combined_output, "pattern_source_used") or str(action.get("inputs", {}).get("pattern_source") or "none")
    plain_english = "The config workflow completed."
    if validation_result == "success" and task == "cleanup":
        plain_english = "Cleaned up the config and kept the changes because validation passed."
    elif validation_result == "success" and task == "validate":
        plain_english = "The config file validated successfully."
    elif validation_result == "success" and task == "compare":
        plain_english = "Compared the two configs and reported the diff summary."
    elif validation_result == "success" and task == "generate":
        plain_english = "Generated a new config file and validated it successfully."
    elif validation_result == "success" and task == "align":
        plain_english = "Aligned the config with the selected style hints and validated it successfully."
    elif blocked_reason:
        plain_english = "The config workflow was blocked because validation failed or the inputs were unsafe."
    interactive_print_result(
        action.get("workflow") or "Work with a config file",
        success=validation_result in {"success", "skipped"},
        fields=[
            ("config_path", action.get("inputs", {}).get("config_path") or "(unknown)"),
            ("config_type", config_type),
            ("task", task),
            ("validation_command", validation_command or "(none)"),
            ("validation_result", validation_result),
            ("changes_made", changes_made),
            ("confidence", confidence),
            ("pattern_source_used", pattern_source_used),
        ],
        blocked_reason=blocked_reason if validation_result not in {"success", "skipped"} else "",
        next_step="Adjust the validation command or config inputs, then rerun the config workflow." if validation_result not in {"success", "skipped"} else "",
        what_happened=plain_english,
    )


def interactive_config_action(session: dict) -> dict:
    quick_mode = interactive_workflow_mode(session) == "quick"
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    repo_path = Path(repo).expanduser()
    config_path_value = interactive_prompt_text("Config path:")
    config_path = Path(config_path_value).expanduser()
    if not config_path.is_absolute():
        config_path = (repo_path / config_path).resolve()
    existing_content = ""
    if config_path.exists() and config_path.is_file():
        try:
            existing_content = config_path.read_text()
        except OSError:
            existing_content = ""
    detected = classify_config_file(config_path, existing_content)
    selected_type = "auto" if quick_mode else interactive_prompt_select(
        "How should this config type be chosen?",
        [
            ("auto", "Auto-detect (recommended)"),
            ("nginx", "NGINX"),
            ("reverse_proxy", "Generic reverse proxy"),
            ("php_ini", "php.ini"),
            ("php_fpm_pool", "PHP-FPM pool"),
        ],
        default_key="auto",
    )
    resolved_type = str(detected.get("config_type") or "reverse_proxy") if selected_type == "auto" else selected_type
    task = interactive_prompt_select(
        "What should happen to this config?",
        [
            ("validate", "Validate"),
            ("cleanup", "Clean up / normalize"),
            ("compare", "Compare"),
            ("generate", "Generate new"),
            ("align", "Align with known-good style"),
        ],
        default_key="validate" if quick_mode else "cleanup",
    )
    compare_path = ""
    if task in {"compare", "align"}:
        prompt = "Config to compare against:" if task == "compare" else "Known-good style reference (optional):"
        compare_path = interactive_prompt_text(prompt, "")
    default_validation = default_config_validation_command(config_path, resolved_type)
    validation_mode = "auto" if quick_mode else interactive_prompt_select(
        "How should this config be validated?",
        [
            ("auto", "Auto-detect (recommended)"),
            ("default", "Use default command"),
            ("custom", "Enter custom command"),
            ("skip", "Skip validation"),
        ],
        default_key="auto" if task != "compare" else "skip",
    )
    validation_command = ""
    if validation_mode == "custom":
        validation_command = interactive_prompt_text("Custom validation command:")
    elif validation_mode in {"auto", "default"}:
        validation_command = default_validation
    pattern_source = "none"
    pattern_args: list[str] = []
    if task in {"generate", "align"}:
        if quick_mode:
            pattern_source = "auto"
        else:
            pattern_source, pattern_args = interactive_select_pattern_repo(session, "Pattern source:")
    advanced_options = False
    notes: list[str] = [
        f"classification_source: {detected.get('classification_source', 'fallback')}",
        f"detection_confidence: {detected.get('confidence', 'low')}",
    ]
    if not quick_mode and interactive_prompt_yes_no("Show advanced options?", default=False):
        advanced_options = True
        if task == "generate" and interactive_prompt_yes_no("Skip validation for generated config?", default=False):
            validation_mode = "skip"
            validation_command = ""
        if task in {"cleanup", "align"} and interactive_prompt_yes_no("Use a custom compare/style reference path?", default=False):
            compare_path = interactive_prompt_text("Reference config path:", compare_path)
    args = build_interactive_common_args(session, repo=repo, include_proxy=False, include_output=False)
    args.extend(["--config-file", str(config_path), "--config-task", task, "--config-type", selected_type])
    if compare_path:
        args.extend(["--config-compare", compare_path])
    if validation_mode != "skip" and validation_command:
        args.extend(["--config-validation-cmd", validation_command])
    if pattern_args:
        args.extend(pattern_args)
    confidence = detect_config_task_confidence(
        resolved_type,
        str(detected.get("confidence", "low")),
        pattern_source,
        validation_command,
    )
    return {
        "workflow": "Work with a config file",
        "description": "Use this to validate, clean up, compare, align, or generate config files without restarting services.",
        "scaffolded": False,
        "inputs": {
            "repo": repo,
            "config_path": str(config_path),
            "config_type": resolved_type,
            "task": task,
            "validation_command": validation_command or "(none)",
            "pattern_source": pattern_source,
            "confidence": confidence,
            "workflow_mode": "quick" if quick_mode else "guided",
            "advanced_options": advanced_options,
        },
        "notes": notes + ([f"compare_path: {compare_path}"] if compare_path else []),
        "commands": [
            {
                "label": "work with config",
                "args": args,
                "compact_preview": f"{task} the {resolved_type} config",
            }
        ],
        "result_renderer": render_interactive_config_result,
    }


def interactive_import_training_action(session: dict) -> dict:
    quick_mode = interactive_workflow_mode(session) == "quick"
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    while True:
        source_type = interactive_prompt_select(
            "Where is the source script?",
            [("local", "Local file (recommended)"), ("ssh", "SSH path"), ("http", "HTTP/HTTPS URL")],
            default_key="local",
        )
        source_value = interactive_prompt_text("Source path or URL:")
        target_repo_label, target_repo_path, pattern_args = interactive_select_training_repo(session)
        raw_tags = "" if quick_mode else interactive_prompt_text("Pattern tags (comma-separated):", "")
        pattern_tags = [item.strip() for item in raw_tags.split(",") if item.strip()]
        pattern_type_hint = "" if quick_mode else interactive_prompt_text("Pattern type hint (optional):", "")
        trust_level = "trusted" if quick_mode else interactive_prompt_select(
            "How much trust should this source get?",
            [("trusted", "Trusted"), ("experimental", "Experimental")],
            default_key="trusted",
        )
        print("trust_level_help: trusted = used by default in runs; experimental = weaker influence")
        sanitize_before_import = True if quick_mode else interactive_prompt_yes_no("Sanitize before import?", default=True)
        validate_before_promote = True if quick_mode else interactive_prompt_yes_no("Validate and repair before promotion?", default=True)
        allow_auto_fix = True if quick_mode else interactive_prompt_yes_no("Allow auto-fix during validation?", default=True)
        note = ""
        if not quick_mode and interactive_prompt_yes_no("Show advanced options?", default=False):
            note = interactive_prompt_text("Optional note:", "")
        preflight = interactive_prepare_training_import(
            source_value=source_value,
            source_type=source_type,
            target_repo=target_repo_path,
            requested_trust=trust_level,
            sanitize_before_import=sanitize_before_import,
            validate_before_promote=validate_before_promote,
            allow_auto_fix=allow_auto_fix,
            pattern_tags=pattern_tags,
            pattern_type_hint=pattern_type_hint,
        )
        if not preflight.get("ok"):
            print("\n=== IMPORT PRECHECK ===")
            print(f"source_type: {preflight.get('source_type')}")
            print(f"source_origin: {preflight.get('source_origin')}")
            print(f"acquisition_method: {preflight.get('acquisition_method')}")
            print(f"proxy_used: {format_bool(preflight.get('proxy_used'))}")
            print(f"blocked_reason: {preflight.get('blocked_reason') or 'acquisition failed'}")
            retry_action = interactive_prompt_select(
                "Acquisition failed:",
                [("retry", "Retry"), ("back", "Back to main menu"), ("cancel", "Cancel import")],
                default_key="retry",
            )
            if retry_action == "retry":
                continue
            if retry_action == "cancel":
                return {
                    "workflow": "Import a script into training",
                    "description": "Use this to feed a local, SSH, or URL-backed script into the private training repo.",
                    "scaffolded": False,
                    "inputs": {"repo": repo, "source": source_value, "source_type": source_type},
                    "notes": ["Import cancelled after acquisition failure."],
                    "commands": [],
                    "app_navigation": "cancel",
                }
            return {
                "workflow": "Import a script into training",
                "description": "Use this to feed a local, SSH, or URL-backed script into the private training repo.",
                "scaffolded": False,
                "inputs": {"repo": repo, "source": source_value, "source_type": source_type},
                "notes": ["Import precheck failed; return to the main menu to try another source."],
                "commands": [],
                "app_navigation": "back",
            }
        final_trust = trust_level
        promotion_warning = ""
        if trust_level == "trusted" and (not preflight.get("safe_for_trusted")):
            fallback = interactive_prompt_select(
                "Trusted promotion is not safe for this script:",
                [("experimental", "Downgrade to experimental"), ("abort", "Abort import")],
                default_key="experimental",
            )
            if fallback == "abort":
                return {
                    "workflow": "Import a script into training",
                    "description": "Use this to feed a local, SSH, or URL-backed script into the private training repo.",
                    "scaffolded": False,
                    "inputs": {"repo": repo, "source": source_value, "source_type": source_type},
                    "notes": ["Import aborted because trusted promotion was not safe."],
                    "commands": [],
                    "app_navigation": "back",
                }
            final_trust = "experimental"
            promotion_warning = "Trusted promotion was blocked; the script will be imported with experimental trust."
        command = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False)
        command.extend(["--import-pattern-files", source_value, "--pattern-trust", final_trust])
        command.extend(pattern_args)
        if pattern_tags:
            command.extend(["--pattern-tags", ",".join(pattern_tags)])
        note_parts = []
        if pattern_type_hint:
            note_parts.append(f"pattern_type_hint={pattern_type_hint}")
        if note:
            note_parts.append(note)
        if note_parts:
            command.extend(["--pattern-note", "; ".join(note_parts)])
        notes = [
            f"acquisition_method: {preflight.get('acquisition_method')}",
            f"proxy_used: {format_bool(preflight.get('proxy_used'))}",
            f"sanitized_changed: {format_bool(preflight.get('sanitized_changed'))}",
            f"validation_result: {preflight.get('validation_result')}",
            f"repair_result: {preflight.get('repair_result')}",
        ]
        if promotion_warning:
            notes.append(promotion_warning)
        notes.extend([str(item) for item in preflight.get("warnings") or []])
        return {
            "workflow": "Import a script into training",
            "description": "Use this to feed a local, SSH, or URL-backed script into the private training repo.",
            "scaffolded": False,
            "inputs": {
                "repo": repo,
                "source_type": source_type,
                "source": source_value,
                "training_repo": target_repo_label,
                "target_repo": str(target_repo_path),
                "sanitized": bool(preflight.get("sanitization_applied")),
                "validation_result": preflight.get("validation_result"),
                "validation_command": preflight.get("validation_command") or "(auto)",
                "repair_result": preflight.get("repair_result"),
                "pattern_type": preflight.get("pattern_type") or "(unknown)",
                "applicability_context": preflight.get("applicability_context") or [],
                "tags": pattern_tags or [],
                "trust_level": final_trust,
                "confidence": preflight.get("confidence_level"),
                "workflow_mode": "quick" if quick_mode else "guided",
            },
            "notes": notes,
            "commands": [{"label": "import training source", "args": command, "compact_preview": f"Import into training as {final_trust}"}],
            "result_context": {
                "final_trust": final_trust,
                "warnings": preflight.get("warnings") or [],
                "validation_result": preflight.get("validation_result"),
                "repair_result": preflight.get("repair_result"),
            },
            "result_renderer": render_interactive_import_training_result,
        }


def interactive_inspect_patterns_action(session: dict) -> dict:
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    pattern_repo_mode, pattern_args = interactive_select_pattern_repo(session, "Pattern repo:")
    inspect_target = interactive_prompt_select(
        "Inspect:",
        [("patterns", "Learned patterns"), ("sources", "Pattern sources")],
        default_key="patterns",
    )
    command = build_interactive_common_args(session, repo=repo, include_proxy=False, include_output=True)
    command.extend(pattern_args)
    command.append("--list-patterns" if inspect_target == "patterns" else "--list-pattern-sources")
    return {
        "workflow": "Inspect learned patterns",
        "description": "Use this to review learned patterns or pattern sources without changing them.",
        "scaffolded": True,
        "inputs": {
            "repo": repo,
            "pattern_repo": pattern_repo_mode,
            "inspect_target": inspect_target,
        },
        "notes": [interactive_standard_scaffold_note()],
        "commands": [{"label": "inspect patterns", "args": command}],
    }


def interactive_manage_patterns_action(session: dict) -> dict:
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    pattern_repo_mode, pattern_args = interactive_select_pattern_repo(session, "Pattern repo:")
    target_kind = interactive_prompt_select(
        "Manage target:",
        [("pattern", "Pattern"), ("source", "Source")],
        default_key="pattern",
    )
    action = interactive_prompt_select(
        "Action:",
        [("promote", "Promote"), ("demote", "Demote"), ("forget", "Forget")],
        default_key="promote",
    )
    identifier = interactive_prompt_text("Pattern/source id or path:")
    flag_name = {
        ("pattern", "promote"): "--promote-pattern",
        ("pattern", "demote"): "--demote-pattern",
        ("pattern", "forget"): "--forget-pattern",
        ("source", "promote"): "--promote-source",
        ("source", "demote"): "--demote-source",
        ("source", "forget"): "--forget-source",
    }[(target_kind, action)]
    command = build_interactive_common_args(session, repo=repo, include_proxy=False, include_output=True)
    command.extend(pattern_args)
    command.extend([flag_name, identifier])
    return {
        "workflow": "Manage patterns",
        "description": "Use this to promote, demote, or forget a pattern or source through the existing controls.",
        "scaffolded": True,
        "inputs": {
            "repo": repo,
            "pattern_repo": pattern_repo_mode,
            "target_kind": target_kind,
            "action": action,
            "identifier": identifier,
        },
        "notes": [interactive_standard_scaffold_note()],
        "commands": [{"label": "manage patterns", "args": command}],
    }


def interactive_probe_action(session: dict) -> dict:
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    endpoint = interactive_prompt_text("Endpoint / URL:")
    endpoint_type = interactive_prompt_select(
        "Probe type:",
        [("api", "API"), ("m3u8", "M3U8"), ("auto", "Auto-detect")],
        default_key="auto",
    )
    use_proxy = interactive_prompt_yes_no("Use proxy settings?", default=bool(session.get("http_proxy") or session.get("https_proxy")))
    probe_type = {"api": "json_summary", "m3u8": "m3u8_summary", "auto": "auto"}[endpoint_type]
    command = build_interactive_common_args(session, repo=repo, include_proxy=use_proxy, include_output=True)
    command.extend(["--probe-url", endpoint, "--probe-type", probe_type])
    return {
        "workflow": "Probe API / M3U8 endpoint",
        "description": "Use this when endpoint truth matters for debugging or validation. It is only one workflow in the app.",
        "scaffolded": True,
        "inputs": {
            "repo": repo,
            "endpoint": endpoint,
            "endpoint_type": endpoint_type,
            "use_proxy": use_proxy,
        },
        "notes": [
            "Probe output is bounded and secrets are redacted before printing.",
            interactive_standard_scaffold_note(),
        ],
        "commands": [{"label": "probe endpoint", "args": command}],
    }


def interactive_sync_conflicts_action(session: dict) -> dict:
    repo = interactive_prompt_text("Repo path:", str(session.get("repo") or Path.cwd()))
    sync_mode = interactive_prompt_select(
        "Conflict action:",
        [("check", "Check/report conflicts"), ("repair", "Repair conflicts with defaults")],
        default_key="check",
    )
    command = build_interactive_common_args(session, repo=repo, include_proxy=True, include_output=False)
    command.append("--sync-conflicts")
    if sync_mode == "check":
        command.append("--no-auto-merge-conflicts")
    return {
        "workflow": "Sync/repair repo conflicts",
        "description": "Use this to inspect or repair merge-conflict states through the conflict-handling backend.",
        "scaffolded": True,
        "inputs": {
            "repo": repo,
            "conflict_action": sync_mode,
        },
        "notes": [
            "This uses the existing merge-conflict repair engine and blocks when conflicts remain ambiguous.",
            interactive_standard_scaffold_note(),
        ],
        "commands": [{"label": "sync/repair conflicts", "args": command}],
    }


def interactive_update_settings(session: dict) -> None:
    interactive_section("Settings")
    session["repo"] = interactive_prompt_text("Default repo path:", str(session.get("repo") or Path.cwd()))
    session["http_proxy"] = interactive_prompt_text("Default HTTP proxy:", str(session.get("http_proxy") or ""))
    session["https_proxy"] = interactive_prompt_text("Default HTTPS proxy:", str(session.get("https_proxy") or ""))
    session["output"] = interactive_prompt_select(
        "Default output mode:",
        [("human", "Human"), ("json", "JSON")],
        default_key=str(session.get("output") or "human"),
    )
    session["interaction_mode"] = interactive_prompt_select(
        "Default workflow mode:",
        [("guided", "Guided (recommended)"), ("quick", "Quick")],
        default_key=str(session.get("interaction_mode") or "guided"),
    )
    print("settings_updated: true")


def run_interactive_app(initial_session: dict | None = None) -> int:
    if not sys.stdin.isatty():
        print("Interactive mode requires a terminal.", file=sys.stderr)
        return 2
    session = {
        "repo": str((initial_session or {}).get("repo") or Path.cwd()),
        "http_proxy": str((initial_session or {}).get("http_proxy") or ""),
        "https_proxy": str((initial_session or {}).get("https_proxy") or ""),
        "output": str((initial_session or {}).get("output") or "human"),
        "interaction_mode": str((initial_session or {}).get("interaction_mode") or "guided"),
    }
    registry = interactive_workflow_registry()
    while True:
        print_interactive_header(session)
        selection = interactive_prompt_select(
            "\nMain menu:",
            [
                (key, f"{spec['label']} - {spec['description']}")
                for key, spec in registry.items()
            ],
            default_key="fix_validate",
        )
        if selection == "exit":
            print("Exiting interactive mode.")
            return 0
        if selection == "settings":
            print(f"\nwhen_to_use: {registry['settings']['description']}")
            interactive_update_settings(session)
            continue
        spec = registry[selection]
        print(f"\nwhen_to_use: {spec['description']}")
        action = spec["handler"](session)
        if action.get("app_navigation") == "cancel":
            print("Exiting interactive mode.")
            return 0
        if action.get("app_navigation") == "back":
            continue
        print_interactive_action_summary(action)
        next_step = interactive_next_step_prompt()
        if next_step == "cancel":
            print("Exiting interactive mode.")
            return 0
        if next_step == "back":
            continue
        if next_step == "run":
            code = execute_interactive_action(action)
            if code != 0 and action.get("workflow") == "Publish current repo state":
                followup = interactive_handle_publish_blocked_followup(action)
                if followup == "cancel":
                    print("Exiting interactive mode.")
                    return code
                if followup == "back":
                    continue
            if code != 0 and not interactive_prompt_yes_no("Return to the main menu?", default=True):
                return code
        if not interactive_prompt_yes_no("Return to the main menu?", default=True):
            return 0


def detect_default_branch(repo: Path) -> str:
    code, output = run_subprocess(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], repo)
    if code == 0 and output.strip():
        return output.strip().rsplit("/", 1)[-1]
    for candidate in ["main", "master"]:
        code, _ = run_subprocess(["git", "rev-parse", "--verify", candidate], repo)
        if code == 0:
            return candidate
    return "main"


def parse_remote_names(repo: Path) -> list[str]:
    code, output = run_subprocess(["git", "remote"], repo)
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def origin_remote_url(repo: Path) -> str:
    code, output = run_subprocess(["git", "remote", "get-url", "origin"], repo)
    return output.strip() if code == 0 else ""


def detect_github_transport(remote_url: str) -> str:
    text = (remote_url or "").strip().lower()
    if text.startswith("https://github.com/"):
        return "https"
    if text.startswith("git@github.com:") or text.startswith("ssh://git@github.com/"):
        return "ssh"
    return "unknown"


def parse_github_remote(remote_url: str) -> tuple[str, str]:
    text = (remote_url or "").strip()
    patterns = [
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group("owner"), match.group("repo")
    return "", ""


def make_github_ssh_remote(owner: str, repo_name: str) -> str:
    if not owner or not repo_name:
        return ""
    return f"git@github.com:{owner}/{repo_name}.git"


def normalize_github_remote_url(remote_url: str, preferred_transport: str = "ssh") -> dict:
    owner, repo_name = parse_github_remote(remote_url)
    transport = detect_github_transport(remote_url)
    normalized = (remote_url or "").strip()
    changed = False
    valid = bool(owner and repo_name)
    if valid:
        if preferred_transport == "ssh":
            canonical = make_github_ssh_remote(owner, repo_name)
        else:
            canonical = f"https://github.com/{owner}/{repo_name}.git"
        changed = canonical != normalized
        normalized = canonical
        transport = detect_github_transport(normalized)
    return {
        "original": (remote_url or "").strip(),
        "normalized": normalized,
        "changed": changed,
        "valid": valid,
        "owner": owner,
        "repo": repo_name,
        "transport": transport,
    }


def set_origin_remote_url(repo: Path, new_url: str) -> tuple[bool, str]:
    code, output = run_subprocess(["git", "remote", "set-url", "origin", new_url], repo)
    return code == 0, output


def parse_github_user_from_auth_output(output: str) -> str:
    for line in (output or "").splitlines():
        match = re.search(r"account\s+(\S+)", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"logged in to github\.com account (\S+)", line, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def github_auth_status(repo: Path) -> tuple[bool, str]:
    code, output = run_subprocess(["gh", "auth", "status"], repo)
    return code == 0, output.strip()


def detect_current_github_user(repo: Path, auth_output: str = "") -> str:
    code, output = run_subprocess(["gh", "api", "user", "--jq", ".login"], repo)
    if code == 0 and output.strip():
        return output.strip()
    if auth_output:
        return parse_github_user_from_auth_output(auth_output)
    _, status_output = github_auth_status(repo)
    return parse_github_user_from_auth_output(status_output)


def probe_github_ssh_auth() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "git@github.com"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "SSH auth probe to git@github.com timed out."
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    lowered = output.lower()
    ssh_ok = any(
        token in lowered
        for token in [
            "successfully authenticated",
            "you've successfully authenticated",
        ]
    )
    return ssh_ok, output


def github_cli_available(repo: Path) -> bool:
    code, _ = run_subprocess(["gh", "--version"], repo)
    return code == 0


def build_publish_preflight(repo: Path, branch: str) -> dict:
    remote_url = origin_remote_url(repo)
    transport = detect_github_transport(remote_url)
    origin_owner, origin_repo = parse_github_remote(remote_url)
    remotes = parse_remote_names(repo)
    upstream_url = ""
    upstream_owner = ""
    upstream_repo = ""
    if "upstream" in remotes:
        code, output = run_subprocess(["git", "remote", "get-url", "upstream"], repo)
        if code == 0 and output.strip():
            upstream_url = output.strip()
            upstream_owner, upstream_repo = parse_github_remote(upstream_url)
    gh_available = github_cli_available(repo)
    gh_auth = False
    gh_auth_output = ""
    current_user = ""
    if gh_available:
        gh_auth, gh_auth_output = github_auth_status(repo)
        current_user = detect_current_github_user(repo, gh_auth_output)
    ssh_auth, ssh_output = probe_github_ssh_auth()
    owner_mismatch = bool(origin_owner and current_user and origin_owner.lower() != current_user.lower())
    return {
        "branch": branch,
        "transport": transport,
        "gh_available": gh_available,
        "gh_auth": gh_auth,
        "ssh_auth": ssh_auth,
        "origin_url": remote_url,
        "origin_owner": origin_owner,
        "origin_repo": origin_repo,
        "current_user": current_user,
        "requires_fork": owner_mismatch,
        "upstream_present": "upstream" in remotes,
        "upstream_url": upstream_url,
        "upstream_owner": upstream_owner,
        "upstream_repo": upstream_repo,
        "gh_auth_output": gh_auth_output,
        "ssh_auth_output": ssh_output,
    }


def apply_origin_normalization(repo: Path, result: dict, publish_state: dict) -> tuple[bool, str]:
    preferred_transport = "ssh" if publish_state.get("ssh_confirmed") or result.get("transport_locked") else "ssh"
    normalized = normalize_github_remote_url(result["preflight"].get("origin_url", ""), preferred_transport=preferred_transport)
    result["normalized_origin"] = normalized["normalized"]
    if not normalized["valid"]:
        return False, "Publish blocked because origin is malformed or could not be normalized to a GitHub remote."
    if normalized["changed"]:
        print("Publish normalization: canonicalizing origin.")
        print(f"origin before: {normalized['original']}")
        print(f"origin after:  {normalized['normalized']}")
        changed, output = set_origin_remote_url(repo, normalized["normalized"])
        if not changed:
            return False, f"Publish blocked because origin normalization failed: {output}"
        result["actions"].append("normalized origin")
        result["preflight"]["origin_url"] = normalized["normalized"]
        result["preflight"]["transport"] = normalized["transport"]
        result["preflight"]["origin_owner"] = normalized["owner"]
        result["preflight"]["origin_repo"] = normalized["repo"]
    return True, ""


def detect_publish_environment() -> dict:
    ci = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
    github_actions = os.environ.get("GITHUB_ACTIONS", "").strip().lower() in {"1", "true", "yes"}
    interactive = sys.stdin.isatty()
    allow_auto_fork = os.environ.get("AI_PUBLISH_ALLOW_FORK", "").strip().lower() in {"1", "true", "yes"}
    return {
        "ci": ci,
        "github_actions": github_actions,
        "interactive": interactive,
        "allow_auto_fork": allow_auto_fork,
    }


def recommended_publish_command(include_pr: bool = True) -> str:
    if include_pr:
        return "./scripts/fixpublish.sh"
    return "./scripts/publishcurrent.sh"


def recommended_publish_current_command(include_pr: bool = True) -> str:
    if include_pr:
        return "./scripts/fixpublish.sh"
    return "./scripts/publishcurrent.sh"


def make_publish_result() -> dict:
    return {
        "published": False,
        "validation_state": "success",
        "validation_commit_match": False,
        "fingerprint_match": False,
        "last_validated_commit": "",
        "current_commit": "",
        "validation_age_seconds": -1,
        "forced_publish": False,
        "auto_revalidated": False,
        "validation_reused": False,
        "auto_revalidation_result": "not_needed",
        "validation_stale_detected": False,
        "validation_rerun_attempted": False,
        "validation_rerun_result": "not_needed",
        "validation_commit_updated": False,
        "publish_reason": "",
        "branch": "",
        "commit_sha": "",
        "pr_url": "",
        "pr_created_or_reused": False,
        "pr_merged": False,
        "local_main_synced": False,
        "reason": "",
        "remote_url": "",
        "normalized_origin": "",
        "auth_transport": "unknown",
        "next_action": "",
        "preflight": {
            "transport": "unknown",
            "gh_available": False,
            "gh_auth": False,
            "ssh_auth": False,
            "origin_url": "",
            "origin_owner": "",
            "origin_repo": "",
            "current_user": "",
            "requires_fork": False,
        },
        "state_loaded": False,
        "state_reset": False,
        "reused_fork": False,
        "transport_locked": False,
        "state_confidence": "low",
        "publish_state": {},
        "environment": {
            "ci": False,
            "github_actions": False,
            "interactive": False,
            "allow_auto_fork": False,
        },
        "fingerprint": {
            "matched_previous_success": False,
            "reason": "",
            "commit": "",
            "branch": "",
            "target_repo": "",
        },
        "pr_already_exists": False,
        "recommended_command": "",
        "target": {
            "type": "blocked",
            "remote_name": "origin",
            "repo": "",
            "transport": "unknown",
            "url": "",
            "requires_fork": False,
            "reason": "",
        },
        "control_path": "",
        "actions": [],
        "attempted_fixes": [],
        "retry_performed": False,
        "retry_success": False,
        "retry_reason": "",
        "publish_scope": "validated_run",
        "working_tree": {
            "status_output": "",
            "clean": True,
            "has_unstaged": False,
            "has_staged": False,
            "has_untracked": False,
            "staged_paths": [],
            "unstaged_paths": [],
            "untracked_paths": [],
        },
        "working_tree_clean": True,
        "working_tree_publishable_paths": [],
        "working_tree_internal_paths": [],
        "working_tree_artifact_paths": [],
        "working_tree_other_paths": [],
        "pr_summary": "",
        "summary_status": "",
        "auto_stage_attempted": False,
        "auto_stage_result": "not_needed",
        "auto_staged_paths": [],
        "remaining_unstaged_paths": [],
        "remaining_unstaged": [],
        "blocked_file_analysis": [],
        "blocked_analysis_summary": {},
        "safe_staged_paths": [],
        "ignored_nonblocking_paths": [],
        "safe_stage_candidate_paths": [],
        "true_blockers": [],
        "blocker_count": 0,
        "publishable_ready": False,
        "blocker_remediation_attempted": False,
        "blocker_remediation_result": "not_needed",
        "auto_removed_paths": [],
        "auto_ignored_patterns": [],
        "remaining_true_blockers": [],
        "file_decisions": [],
        "staging_summary": {
            "auto_staged": 0,
            "ignored": 0,
            "blocked": 0,
        },
        "_auto_stage_entry_state": {},
        "staging_decision_reason": "",
        "staging_reason": "",
        "explain_staging": False,
        "noop": False,
        "requested": False,
        "attempted": False,
        "verification": {
            "current_branch": "",
            "upstream_branch": "",
            "upstream_exists": False,
            "local_head": "",
            "remote_head": "",
            "synced": False,
            "reason": "",
        },
        "pr_requested": False,
        "pr_status": "not_requested",
        "pr_reason": "",
        "pr_mergeable": "unknown",
        "pr_conflicts_detected": False,
        "pr_mergeability_reason": "",
        "pr_mergeability_source": "github",
        "pr_mergeable_final": "unknown",
        "pr_conflicts_detected_final": False,
        "pr_mergeability_repair_attempted": False,
        "pr_mergeability_repair_result": "not_needed",
        "prepublish_base_alignment_attempted": False,
        "base_branch": "",
        "branch_diverged": False,
        "alignment_needed": False,
        "alignment_result": "not_needed",
        "alignment_changed_commit": False,
        "validation_rerun_after_alignment": False,
        "alignment_block_reason": "",
        "final_workflow_result": "",
        "merge_conflict_result": None,
        "triggered": False,
        "meaningful_changes_detected": False,
        "meaningful_paths": [],
        "ignored_changes": [],
        "last_published_commit": "",
        "current_publish_candidate_commit": "",
        "diff_files_detected": [],
        "docs_checked_at_publish": False,
        "docs_check_performed": False,
        "docs_status": "up_to_date",
        "docs_reason": "documentation check not performed",
        "docs_required": False,
        "docs_updated": False,
        "docs_refresh_mode": "none",
        "docs_targets": [],
        "previous_publish_branch": "",
        "previous_pr_url": "",
        "previous_commit": "",
        "final": {
            "status": "failed",
            "branch": "",
            "commit": "",
            "remote": "",
            "pr_url": None,
        },
    }


def set_publish_final(result: dict, status: str, branch: str = "", commit: str = "", remote: str = "", pr_url: str | None = None) -> None:
    result["published"] = status == "success"
    result["branch"] = branch
    result["commit_sha"] = commit
    result["pr_url"] = pr_url or ""
    result["final"] = {
        "status": status,
        "branch": branch,
        "commit": commit,
        "remote": remote,
        "pr_url": pr_url,
    }


GITHUB_PR_URL_RE = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+")


def extract_github_pr_url(output: str) -> str:
    matches = GITHUB_PR_URL_RE.findall(str(output or ""))
    return matches[-1].strip() if matches else ""


def extract_json_payload(output: str) -> str:
    text = str(output or "").strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    return text


def summarize_post_publish_working_tree(repo: Path) -> dict:
    working_tree = classify_publish_working_tree(repo)
    status_output = str(working_tree.get("status_output") or "")
    entries = collect_publish_working_tree_entries(repo, status_output=status_output)
    publishable_paths: list[str] = []
    internal_paths: list[str] = []
    artifact_paths: list[str] = []
    other_paths: list[str] = []
    for entry in entries:
        path = str(entry.get("path") or "")
        file_type = str(entry.get("file_type") or "unknown")
        if not path:
            continue
        if is_publish_ignored_change_path(path) or file_type == "state":
            internal_paths.append(path)
            continue
        if bool(entry.get("publishable")):
            publishable_paths.append(path)
            continue
        if file_type in {"artifact", "generated"}:
            artifact_paths.append(path)
            continue
        other_paths.append(path)
    return {
        **working_tree,
        "working_tree_clean": not bool(publishable_paths or other_paths),
        "publishable_paths": publishable_paths,
        "internal_paths": internal_paths,
        "artifact_paths": artifact_paths,
        "other_paths": other_paths,
    }


def update_post_publish_reporting(repo: Path, result: dict, branch: str) -> None:
    result["working_tree"] = summarize_post_publish_working_tree(repo)
    result["working_tree_clean"] = bool((result.get("working_tree") or {}).get("working_tree_clean"))
    result["verification"] = verify_publish_sync(repo, branch, "origin")


def normalize_publish_pr_fields(
    repo: Path,
    result: dict,
    *,
    branch: str,
    publish_pr_requested: bool,
    publish_state: dict | None = None,
    hinted_pr_url: str = "",
) -> str:
    candidate_urls = [
        hinted_pr_url,
        str(result.get("pr_url") or ""),
        extract_github_pr_url(str((result.get("verification") or {}).get("reason") or "")),
    ]
    try:
        candidate_urls.append(detect_existing_pr(repo, branch))
    except Exception:
        pass
    if isinstance(publish_state, dict):
        candidate_urls.append(str(publish_state.get("last_pr_url") or ""))
    pr_url = ""
    for candidate in candidate_urls:
        normalized = extract_github_pr_url(candidate) or str(candidate or "").strip()
        if normalized.startswith("https://github.com/") and "/pull/" in normalized:
            pr_url = normalized
            break
    result["pr_url"] = pr_url
    result["pr_created_or_reused"] = bool(result.get("pr_created_or_reused") or result.get("pr_already_exists") or pr_url)
    if pr_url:
        result["pr_status"] = "reused" if result.get("pr_already_exists") else (result.get("pr_status") or "created")
        result["pr_summary"] = f"A PR was created/reused at {pr_url}"
        return pr_url
    if publish_pr_requested:
        result["pr_summary"] = str(result.get("pr_reason") or "No PR URL is available for this successful publish.")
    else:
        result["pr_summary"] = "No PR created (expected for this run)"
    return ""


def set_publish_outcome(result: dict, status: str, reason: str, next_action: str = "") -> dict:
    result["noop"] = status == "noop"
    result["reason"] = reason
    if status == "noop":
        result["publish_reason"] = "noop"
    if reason:
        result["summary_status"] = reason
    result["next_action"] = next_action
    set_publish_final(
        result,
        status,
        branch=result.get("branch", ""),
        commit=result.get("commit_sha", ""),
        remote=result.get("remote_url", ""),
        pr_url=result.get("pr_url") or None,
    )
    return result


def mark_publish_noop(result: dict, reason: str, branch: str, remote: str, commit_sha: str = "") -> dict:
    result["noop"] = True
    result["publish_reason"] = "noop"
    result["control_path"] = "noop"
    result["reason"] = reason
    result["summary_status"] = reason
    result["next_action"] = ""
    result["commit_sha"] = commit_sha
    set_publish_final(result, "noop", branch=branch, commit=commit_sha, remote=remote, pr_url=None)
    return result


def remote_branch_head(repo: Path, remote_ref: str, branch: str) -> tuple[int, str, str]:
    code, output = run_subprocess(["git", "ls-remote", "--heads", remote_ref, branch], repo)
    if code != 0:
        return code, "", output
    line = output.strip().splitlines()[0] if output.strip() else ""
    if not line:
        return 0, "", ""
    sha = line.split()[0]
    return 0, sha, output


def branch_already_up_to_date(repo: Path, branch: str, remote_ref: str = "origin") -> tuple[bool, str]:
    local_head = parse_head_commit(repo)
    if not local_head:
        return False, ""
    code, remote_sha, _ = remote_branch_head(repo, remote_ref, branch)
    if code != 0 or not remote_sha:
        return False, local_head
    return remote_sha == local_head, local_head


def current_upstream_branch(repo: Path, branch: str) -> str:
    code, output = run_subprocess(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{upstream}}"],
        repo,
    )
    if code != 0:
        return ""
    return output.strip()


def verify_publish_sync(repo: Path, branch: str, remote_ref: str = "origin") -> dict:
    current_branch = current_git_branch(repo)
    local_head = parse_head_commit(repo)
    upstream_branch = current_upstream_branch(repo, branch)
    code, remote_head, output = remote_branch_head(repo, remote_ref, branch)
    upstream_exists = code == 0 and bool(remote_head)
    synced = bool(local_head and remote_head and local_head == remote_head)
    reason = ""
    if not branch:
        reason = "publish verification failed: branch name is empty"
    elif not upstream_branch:
        reason = f"publish verification failed: no upstream is configured for branch '{branch}'"
    elif not upstream_exists:
        if code != 0:
            reason = f"publish verification failed: could not read remote branch '{branch}': {output.strip()}"
        else:
            reason = f"publish verification failed: upstream branch '{branch}' does not exist on {remote_ref}"
    elif not synced:
        reason = (
            f"publish verification failed: local HEAD {local_head or '(none)'} "
            f"does not match {remote_ref}/{branch} {remote_head or '(none)'}"
        )
    return {
        "current_branch": current_branch,
        "upstream_branch": upstream_branch,
        "upstream_exists": upstream_exists,
        "local_head": local_head,
        "remote_head": remote_head,
        "synced": synced,
        "reason": reason,
    }


def summarize_publish_result(summary: dict) -> str:
    result = summary.get("publish_result_detail") or {}
    if not summary.get("publish_requested"):
        return "not_requested"
    if not summary.get("publish_triggered"):
        status = str((result.get("final") or {}).get("status") or "").strip()
        if status == "noop":
            return "noop"
        if status == "blocked":
            return "blocked"
        return "failed"
    status = str((result.get("final") or {}).get("status") or "").strip()
    if status == "success":
        return "success"
    if status == "noop":
        return "noop"
    if status == "blocked":
        return "blocked"
    if status == "failed_verification":
        return "failed_verification"
    if status:
        return "failed"
    return "failed"


def publish_summary_requires_failure(summary: dict) -> bool:
    final_workflow_result = str(summary.get("final_workflow_result") or "").strip()
    if final_workflow_result == "blocked":
        return True
    publish_result = str(summary.get("publish_result") or "not_requested").strip()
    return publish_result not in {"success", "noop", "not_requested"}


def resolve_publish_requested(args: argparse.Namespace) -> bool:
    if getattr(args, "publish_only", False):
        return True
    if getattr(args, "no_finalize", False):
        return False
    if getattr(args, "no_publish_on_success", False):
        return False
    return True


def format_bool(value: object) -> str:
    return "true" if bool(value) else "false"


def format_final_operator_summary(summary: dict) -> str:
    validation_result = str(summary.get("validation_result") or "failed").strip()
    publish_result = str(summary.get("publish_result") or "not_requested").strip()
    publish_requested = bool(summary.get("publish_requested"))
    reason = str(summary.get("publish_reason") or "").strip().lower()
    detail_reason = str(summary.get("publish_detail_reason") or "").strip().lower()
    previous_pr_url = str(summary.get("previous_pr_url") or "").strip()
    final_workflow_result = str(summary.get("final_workflow_result") or "").strip()
    if final_workflow_result == "blocked" and publish_result == "success":
        return "FINAL: publish succeeded, PR mergeability blocked"
    if validation_result == "success":
        if publish_result == "success":
            return "FINAL: validation succeeded, publish succeeded"
        if publish_result == "noop":
            if "matched previous successful publish fingerprint" in reason:
                if previous_pr_url:
                    return f"FINAL: already published — PR: {previous_pr_url}"
                return "FINAL: already published — reusing previous result"
            if "already published" in reason or "already up to date" in reason or "no changes to publish" in reason:
                return "FINAL: publish noop (already up to date)"
            return "FINAL: validation succeeded, publish noop"
        if publish_result == "blocked":
            return "FINAL: validation succeeded, publish blocked"
        if publish_requested:
            return f"FINAL: validation succeeded, publish {publish_result}"
        return "FINAL: validation succeeded"
    if publish_result == "noop" and "matched previous successful publish fingerprint" in reason:
        if previous_pr_url:
            return f"FINAL: already published — PR: {previous_pr_url}"
        return "FINAL: already published — reusing previous result"
    if validation_result == "blocked":
        if reason == "blocked_by_validation" or "blocked by validation" in detail_reason:
            return "FINAL: validation blocked, publish blocked"
        if publish_result in {"success", "failed", "noop", "blocked", "failed_verification"}:
            return f"FINAL: validation blocked, publish {publish_result}"
        return "FINAL: validation blocked"
    return "FINAL: validation failed"


def fail_incomplete_without_finalization() -> None:
    print("FINALIZATION SKIPPED: --no-finalize")
    print("FINAL: validation succeeded, finalization skipped (incomplete)")
    raise SystemExit(2)


def resolve_publish_target(preflight: dict, publish_state: dict) -> dict:
    origin_owner = preflight.get("origin_owner", "")
    origin_repo = preflight.get("origin_repo", "")
    current_user = preflight.get("current_user", "")
    origin_url = preflight.get("origin_url", "")
    transport = preflight.get("transport", "unknown")
    if not origin_url or not origin_owner or not origin_repo:
        return {
            "type": "blocked",
            "remote_name": "origin",
            "repo": "",
            "transport": transport,
            "url": origin_url,
            "requires_fork": False,
            "reason": "origin_missing_or_invalid",
        }
    if publish_state.get("fork_created") and publish_state.get("fork_repo"):
        fork_repo = publish_state["fork_repo"]
        fork_owner, fork_name = (fork_repo.split("/", 1) + [""])[:2]
        if fork_owner and fork_name and origin_repo == fork_name:
            return {
                "type": "fork",
                "remote_name": "origin",
                "repo": fork_repo,
                "transport": "ssh",
                "url": make_github_ssh_remote(fork_owner, fork_name),
                "requires_fork": True,
                "reason": "reusing persisted fork target",
            }
    if not preflight.get("gh_available") or not preflight.get("gh_auth") or not current_user:
        return {
            "type": "origin",
            "remote_name": "origin",
            "repo": f"{origin_owner}/{origin_repo}",
            "transport": transport,
            "url": origin_url,
            "requires_fork": False,
            "reason": "auth_blocked",
        }
    if current_user.lower() == origin_owner.lower():
        return {
            "type": "origin",
            "remote_name": "origin",
            "repo": f"{origin_owner}/{origin_repo}",
            "transport": transport,
            "url": origin_url,
            "requires_fork": False,
            "reason": "authenticated user owns origin",
        }
    fork_url = make_github_ssh_remote(current_user, origin_repo)
    return {
        "type": "fork",
        "remote_name": "origin",
        "repo": f"{current_user}/{origin_repo}",
        "transport": "ssh",
        "url": fork_url,
        "requires_fork": True,
        "reason": f"origin owner {origin_owner} differs from authenticated user {current_user}",
    }


def compute_state_confidence(result: dict, publish_state: dict, normalization_ok: bool = True) -> str:
    preflight = result.get("preflight", {})
    if result.get("state_reset"):
        return "low"
    if not normalization_ok:
        return "low"
    if not preflight.get("gh_auth") or not preflight.get("ssh_auth"):
        return "low"
    if publish_state.get("fork_created") and not publish_state.get("fork_repo"):
        return "low"
    return "high"


def detect_existing_pr(repo: Path, branch: str) -> str:
    code, output = run_subprocess(["gh", "pr", "list", "--head", branch, "--json", "url"], repo)
    if code != 0:
        return ""
    direct_url = extract_github_pr_url(output)
    if direct_url:
        return direct_url
    try:
        data = json.loads(extract_json_payload(output) or "[]")
    except json.JSONDecodeError:
        return ""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return str(first.get("url") or "").strip()
    return ""


def verify_pr_mergeability(repo: Path, pr_url: str) -> dict:
    if not pr_url.strip():
        return {
            "pr_mergeable": "unknown",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "no PR URL is available",
            "pr_base_branch": "",
            "pr_head_branch": "",
        }
    code, output = run_subprocess(
        ["gh", "pr", "view", pr_url, "--json", "mergeable,mergeStateStatus,baseRefName,headRefName"],
        repo,
    )
    if code != 0:
        return {
            "pr_mergeable": "unknown",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": f"PR mergeability could not be verified: {output.strip()}",
            "pr_base_branch": "",
            "pr_head_branch": "",
        }
    try:
        data = json.loads(extract_json_payload(output) or "{}")
    except json.JSONDecodeError:
        return {
            "pr_mergeable": "unknown",
            "pr_conflicts_detected": False,
            "pr_mergeability_reason": "PR mergeability could not be parsed from gh output",
            "pr_base_branch": "",
            "pr_head_branch": "",
        }
    mergeable_raw = str(data.get("mergeable") or "").strip().upper()
    merge_state = str(data.get("mergeStateStatus") or "").strip().upper()
    conflicts = mergeable_raw == "CONFLICTING" or merge_state in {"DIRTY", "CONFLICTING"}
    if mergeable_raw in {"MERGEABLE", "CONFLICTING", "UNKNOWN"}:
        mergeable = "true" if mergeable_raw == "MERGEABLE" else "false" if mergeable_raw == "CONFLICTING" else "unknown"
    elif isinstance(data.get("mergeable"), bool):
        mergeable = "true" if bool(data.get("mergeable")) else "false"
    else:
        mergeable = "unknown"
    reason = ""
    if conflicts:
        reason = f"PR has merge conflicts against its base branch ({merge_state or mergeable_raw or 'unknown'})"
    elif mergeable == "unknown":
        reason = f"PR mergeability is not yet known ({merge_state or mergeable_raw or 'unknown'})"
    return {
        "pr_mergeable": mergeable,
        "pr_conflicts_detected": conflicts,
        "pr_mergeability_reason": reason,
        "pr_base_branch": str(data.get("baseRefName") or "").strip(),
        "pr_head_branch": str(data.get("headRefName") or "").strip(),
    }


def locally_verify_pr_mergeability(repo: Path, base_branch: str, head_branch: str) -> dict:
    result = {
        "pr_mergeability_source": "local_fallback",
        "pr_mergeable_final": "unknown",
        "pr_conflicts_detected_final": False,
        "pr_mergeability_reason": "",
    }
    if not base_branch or not head_branch:
        result["pr_mergeability_reason"] = "local PR mergeability verification requires both base and head branches"
        return result
    original_branch = current_git_branch(repo)
    switched = False
    if original_branch != head_branch:
        code, output = run_subprocess(["git", "checkout", head_branch], repo)
        if code != 0:
            result["pr_mergeability_reason"] = f"local PR mergeability verification failed during checkout: {output}"
            return result
        switched = True
    try:
        fetch_code, fetch_output = run_subprocess(["git", "fetch", "origin"], repo)
        if fetch_code != 0:
            result["pr_mergeability_reason"] = f"local PR mergeability verification failed during fetch: {fetch_output}"
            return result
        merge_code, merge_output = run_subprocess(["git", "merge", f"origin/{base_branch}", "--no-commit", "--no-ff"], repo)
        conflicted = conflicted_git_paths(repo)
        if merge_code == 0 and not conflicted:
            result["pr_mergeable_final"] = "true"
            result["pr_conflicts_detected_final"] = False
            return result
        if conflicted:
            result["pr_mergeable_final"] = "false"
            result["pr_conflicts_detected_final"] = True
            result["pr_mergeability_reason"] = (
                f"local mergeability check found conflicts against origin/{base_branch}: "
                + ", ".join(conflicted[:10])
            )
            return result
        result["pr_mergeability_reason"] = (
            merge_output.strip()
            or f"local PR mergeability verification failed while merging origin/{base_branch}"
        )
        return result
    finally:
        sequence_state = detect_git_sequence_state(repo)
        if sequence_state == "merge":
            run_subprocess(["git", "merge", "--abort"], repo)
        if switched:
            run_subprocess(["git", "checkout", original_branch], repo)


def resolve_pr_mergeability(repo: Path, pr_url: str) -> dict:
    github = verify_pr_mergeability(repo, pr_url)
    result = {
        "pr_mergeable": github.get("pr_mergeable") or "unknown",
        "pr_conflicts_detected": bool(github.get("pr_conflicts_detected")),
        "pr_mergeability_reason": str(github.get("pr_mergeability_reason") or ""),
        "pr_base_branch": str(github.get("pr_base_branch") or ""),
        "pr_head_branch": str(github.get("pr_head_branch") or ""),
        "pr_mergeability_source": "github",
        "pr_mergeable_final": github.get("pr_mergeable") or "unknown",
        "pr_conflicts_detected_final": bool(github.get("pr_conflicts_detected")),
    }
    base_branch = result["pr_base_branch"]
    head_branch = result["pr_head_branch"]
    if base_branch and head_branch and result["pr_mergeable"] in {"unknown", "true"}:
        local = locally_verify_pr_mergeability(repo, base_branch, head_branch)
        if local.get("pr_mergeable_final") in {"true", "false"}:
            result["pr_mergeability_source"] = "local_fallback"
            result["pr_mergeable_final"] = local.get("pr_mergeable_final") or result["pr_mergeable_final"]
            result["pr_conflicts_detected_final"] = bool(local.get("pr_conflicts_detected_final"))
            if local.get("pr_mergeability_reason"):
                result["pr_mergeability_reason"] = str(local.get("pr_mergeability_reason"))
    return result


def can_auto_merge_publish_result(result: dict) -> tuple[bool, str]:
    target = result.get("target") or {}
    preflight = result.get("preflight") or {}
    current_user = str(preflight.get("current_user") or "").strip().lower()
    origin_owner = str(preflight.get("origin_owner") or "").strip().lower()
    target_repo = str(target.get("repo") or "").strip()
    target_owner = target_repo.split("/", 1)[0].lower() if "/" in target_repo else ""
    if not result.get("pr_url"):
        return False, "Auto-merge skipped because no PR URL is available."
    if target.get("type") != "fork":
        return False, "Auto-merge is only allowed for self-owned fork PRs."
    if not current_user or target_owner != current_user:
        return False, "Auto-merge skipped because the fork owner could not be verified as the authenticated user."
    if not origin_owner or origin_owner == current_user:
        return False, "Auto-merge skipped because the PR does not target an upstream repo from a self-owned fork."
    return True, ""


def merge_published_pr(repo: Path, result: dict) -> tuple[bool, str]:
    ok, reason = can_auto_merge_publish_result(result)
    if not ok:
        return False, reason
    code, output = run_subprocess(["gh", "pr", "merge", result["pr_url"], "--merge", "--delete-branch"], repo)
    if code != 0:
        return False, f"Auto-merge failed: {output}"
    result["pr_merged"] = True
    result["actions"].append("pr merged")
    return True, ""


def repair_pr_mergeability(repo: Path, pr_url: str, validation_command: str = "") -> dict:
    result = {
        "attempted": False,
        "result": "not_needed",
        "reason": "",
        "mergeability": resolve_pr_mergeability(repo, pr_url),
        "merge_conflict_result": None,
    }
    initial = result["mergeability"]
    if not pr_url.strip():
        result["reason"] = "no PR URL is available"
        return result
    if not initial.get("pr_conflicts_detected_final"):
        return result
    base_branch = str(initial.get("pr_base_branch") or "").strip()
    head_branch = str(initial.get("pr_head_branch") or "").strip()
    if not base_branch or not head_branch:
        result["attempted"] = True
        result["result"] = "blocked"
        result["reason"] = "PR mergeability repair blocked because the PR base/head branches could not be determined."
        return result
    result["attempted"] = True
    original_branch = current_git_branch(repo)
    switched = False
    if original_branch != head_branch:
        code, output = run_subprocess(["git", "checkout", head_branch], repo)
        if code != 0:
            result["result"] = "blocked"
            result["reason"] = f"PR mergeability repair blocked because checkout of '{head_branch}' failed: {output}"
            return result
        switched = True
    fetch_code, fetch_output = run_subprocess(["git", "fetch", "origin", base_branch], repo)
    if fetch_code != 0:
        if switched:
            run_subprocess(["git", "checkout", original_branch], repo)
        result["result"] = "blocked"
        result["reason"] = f"PR mergeability repair blocked because fetching base branch '{base_branch}' failed: {fetch_output}"
        return result
    ok, sync_reason, conflict_result = run_sync_operation_with_conflict_hook(
        repo,
        sync_operation="pr_mergeability_repair",
        command=["git", "merge", f"origin/{base_branch}"],
        validation_command=validation_command or latest_repo_validation_command(repo),
        no_auto_conflict_resolution_after_sync=CURRENT_NO_AUTO_CONFLICT_RESOLUTION_AFTER_SYNC,
    )
    result["merge_conflict_result"] = conflict_result
    if not ok:
        if switched:
            run_subprocess(["git", "checkout", original_branch], repo)
        result["result"] = "blocked"
        result["reason"] = sync_reason or str(conflict_result.get("blocked_reason") or "PR mergeability repair failed")
        return result
    push_code, push_output = run_subprocess(["git", "push", "origin", head_branch], repo)
    if switched:
        restore_code, restore_output = run_subprocess(["git", "checkout", original_branch], repo)
        if restore_code != 0:
            result["result"] = "blocked"
            result["reason"] = f"PR mergeability repair succeeded, but restoring branch '{original_branch}' failed: {restore_output}"
            return result
    if push_code != 0:
        result["result"] = "blocked"
        result["reason"] = f"PR mergeability repair blocked because pushing the repaired PR branch failed: {push_output}"
        return result
    refreshed = resolve_pr_mergeability(repo, pr_url)
    result["mergeability"] = refreshed
    if refreshed.get("pr_conflicts_detected_final"):
        result["result"] = "blocked"
        result["reason"] = str(refreshed.get("pr_mergeability_reason") or "PR still has merge conflicts after repair attempt")
        return result
    result["result"] = "success"
    return result


def resolve_prepublish_base_branch(repo: Path, branch: str, default_branch: str) -> tuple[str, str]:
    try:
        existing_pr = detect_existing_pr(repo, branch)
    except Exception:
        existing_pr = ""
    if existing_pr:
        try:
            mergeability = verify_pr_mergeability(repo, existing_pr)
        except Exception:
            mergeability = {}
        pr_base_branch = str(mergeability.get("pr_base_branch") or "").strip()
        if pr_base_branch:
            return pr_base_branch, existing_pr
    return default_branch or "main", existing_pr


def align_branch_with_base_before_publish(
    repo: Path,
    *,
    branch: str,
    base_branch: str,
    validation_command: str = "",
    no_auto_conflict_resolution_after_sync: bool = False,
) -> dict:
    result = {
        "prepublish_base_alignment_attempted": False,
        "base_branch": base_branch or "",
        "branch_diverged": False,
        "alignment_needed": False,
        "alignment_result": "not_needed",
        "alignment_changed_commit": False,
        "validation_rerun_after_alignment": False,
        "alignment_block_reason": "",
        "merge_conflict_result": None,
        "validation_result_after_alignment": "not_run",
        "validation_record_result": None,
        "existing_pr_url": "",
    }
    if not branch or not base_branch:
        result["alignment_result"] = "blocked"
        result["alignment_block_reason"] = "pre-publish base alignment requires both a publish branch and a base branch"
        return result
    current_branch = current_git_branch(repo)
    if current_branch != branch:
        result["alignment_result"] = "blocked"
        result["alignment_block_reason"] = (
            f"pre-publish base alignment expected branch '{branch}', but current branch is '{current_branch or '(none)'}'"
        )
        return result
    fetch_code, fetch_output = run_subprocess(["git", "fetch", "origin", base_branch], repo)
    if fetch_code != 0:
        result["alignment_result"] = "blocked"
        result["alignment_block_reason"] = (
            f"pre-publish base alignment blocked because fetching origin/{base_branch} failed: {fetch_output}"
        )
        return result
    count_code, count_output = run_subprocess(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{base_branch}"],
        repo,
    )
    if count_code != 0:
        result["alignment_result"] = "blocked"
        result["alignment_block_reason"] = (
            count_output.strip() or f"failed to compare HEAD against origin/{base_branch}"
        )
        return result
    ahead_count, behind_count = parse_ahead_behind_counts(count_output)
    result["branch_diverged"] = ahead_count > 0 and behind_count > 0
    result["alignment_needed"] = behind_count > 0
    if not result["alignment_needed"]:
        return result
    result["prepublish_base_alignment_attempted"] = True
    before_commit = parse_head_commit(repo)
    ok, sync_reason, conflict_result = run_sync_operation_with_conflict_hook(
        repo,
        sync_operation="prepublish_base_alignment",
        command=["git", "merge", "--no-edit", f"origin/{base_branch}"],
        validation_command=validation_command or latest_repo_validation_command(repo),
        no_auto_conflict_resolution_after_sync=no_auto_conflict_resolution_after_sync,
    )
    result["merge_conflict_result"] = conflict_result
    after_commit = parse_head_commit(repo)
    result["alignment_changed_commit"] = bool(before_commit and after_commit and before_commit != after_commit)
    if conflict_result.get("merge_conflicts_detected"):
        conflicted_files = conflict_result.get("conflicted_files") or []
        if conflicted_files and not ok and not sync_reason:
            sync_reason = "pre-publish base alignment blocked due to conflicted files: " + ", ".join(conflicted_files[:10])
    if not ok:
        result["alignment_result"] = "blocked"
        result["alignment_block_reason"] = sync_reason or str(conflict_result.get("blocked_reason") or "pre-publish base alignment failed")
        validation_after_merge = str(conflict_result.get("validation_result_after_merge") or "not_run")
        result["validation_result_after_alignment"] = validation_after_merge
        return result
    if result["alignment_changed_commit"]:
        validation_record = ensure_validation_record_for_current_commit(
            repo,
            validation_command=validation_command or latest_repo_validation_command(repo),
        )
        result["validation_rerun_after_alignment"] = True
        result["validation_record_result"] = validation_record
        result["validation_result_after_alignment"] = str(validation_record.get("validation_result") or "blocked")
        if not validation_record.get("ok"):
            result["alignment_result"] = "blocked"
            result["alignment_block_reason"] = (
                validation_record.get("reason")
                or "pre-publish base alignment changed the branch, but validation did not succeed"
            )
            return result
    else:
        validation_after_merge = str(conflict_result.get("validation_result_after_merge") or "not_run")
        result["validation_result_after_alignment"] = validation_after_merge
    result["alignment_result"] = "success"
    return result


def sync_local_main_after_merge(repo: Path) -> tuple[bool, str]:
    current_branch = current_git_branch(repo)
    code, output = run_subprocess(["git", "rev-parse", "--verify", "main"], repo)
    if code != 0:
        return False, "Local main sync skipped because the `main` branch does not exist locally."
    switched = False
    if current_branch != "main":
        code, output = run_subprocess(["git", "checkout", "main"], repo)
        if code != 0:
            return False, f"Local main sync failed during checkout: {output}"
        switched = True
    ok, sync_reason, conflict_result = run_sync_operation_with_conflict_hook(
        repo,
        sync_operation="branch_sync",
        command=["git", "pull", "--ff-only", "origin", "main"],
        validation_command=latest_repo_validation_command(repo),
        no_auto_conflict_resolution_after_sync=CURRENT_NO_AUTO_CONFLICT_RESOLUTION_AFTER_SYNC,
    )
    if switched:
        restore_code, restore_output = run_subprocess(["git", "checkout", current_branch], repo)
        if restore_code != 0:
            return False, f"Local main sync restored main, but could not switch back to {current_branch}: {restore_output}"
    if conflict_result.get("merge_conflicts_detected"):
        if ok:
            return True, ""
        return False, f"Local main sync conflict handling blocked: {sync_reason or conflict_result.get('blocked_reason')}"
    if not ok:
        return False, f"Local main sync failed: {sync_reason}"
    return True, ""


def target_remote_exists(repo: Path, target: dict) -> tuple[bool, str]:
    target_url = target.get("url", "")
    if not target_url:
        return False, ""
    code, output = run_subprocess(["git", "ls-remote", target_url, "HEAD"], repo)
    if code != 0:
        lowered = (output or "").lower()
        if "repository not found" in lowered:
            return False, output
        return False, output
    return bool((output or "").strip()), output


def prepare_publish_target(repo: Path, result: dict) -> tuple[bool, str, str]:
    target = result["target"]
    preflight = result["preflight"]
    environment = result.get("environment", {})
    if target["type"] == "origin":
        if target["transport"] == "https" and preflight.get("gh_auth") and preflight.get("origin_owner") and preflight.get("origin_repo"):
            new_url = make_github_ssh_remote(preflight["origin_owner"], preflight["origin_repo"])
            if new_url and new_url != preflight["origin_url"]:
                print("Publish preflight: rewriting origin from HTTPS to SSH.")
                print(f"origin before: {preflight['origin_url']}")
                print(f"origin after:  {new_url}")
                changed, output = set_origin_remote_url(repo, new_url)
                if not changed:
                    return False, f"Publish blocked because the preflight HTTPS->SSH rewrite failed: {output}", "Verify that `origin` is writable and retry."
                result["actions"].append("preflight origin https->ssh")
                result["attempted_fixes"].append("origin https->ssh")
                preflight["origin_url"] = new_url
                preflight["transport"] = "ssh"
                target["url"] = new_url
                target["transport"] = "ssh"
    elif target["type"] == "fork":
        exists, details = target_remote_exists(repo, target)
        if exists:
            result["reused_fork"] = True
        if not exists:
            if target.get("reason") == "reusing persisted fork target":
                result["state_confidence"] = "low"
                result["reused_fork"] = False
                result["target"] = resolve_publish_target(preflight, {})
                target = result["target"]
                if target["type"] != "fork":
                    result["remote_url"] = target.get("url", result["remote_url"])
                    result["auth_transport"] = target.get("transport", result["auth_transport"])
                    return True, "", ""
            upstream_repo = f"{preflight.get('origin_owner', '')}/{preflight.get('origin_repo', '')}".strip("/")
            if not preflight.get("gh_available"):
                return False, (
                    f"Fork target `{target['repo']}` does not exist yet. "
                    f"Create it with `gh repo fork {upstream_repo} --clone=false` and set origin with "
                    f"`git remote set-url origin {target['url']}`."
                ), "Install/authenticate GitHub CLI or create the fork manually, then rerun."
            if not environment.get("interactive"):
                if environment.get("allow_auto_fork"):
                    print(f"Creating fork for {upstream_repo} in non-interactive mode because AI_PUBLISH_ALLOW_FORK=1...")
                    fork_code, fork_output = run_subprocess(["gh", "repo", "fork", upstream_repo, "--clone=false"], repo)
                    if fork_code != 0:
                        return False, f"Fork creation failed: {fork_output}", "Resolve the fork creation error, then rerun publish."
                    result["actions"].append("fork created with gh")
                    result["attempted_fixes"].append("created fork")
                else:
                    return False, (
                        f"Fork target `{target['repo']}` does not exist yet. "
                        f"Run `gh repo fork {upstream_repo} --clone=false` and `git remote set-url origin {target['url']}`."
                    ), "Re-run interactively or set `AI_PUBLISH_ALLOW_FORK=1` to allow non-interactive fork creation."
            else:
                if not prompt_yes_no("Upstream repo detected. Create fork now?", default=False):
                    return False, "Fork creation was not confirmed.", (
                        f"Create the fork with `gh repo fork {upstream_repo} --clone=false`, then set origin with "
                        f"`git remote set-url origin {target['url']}`."
                    )
                print(f"Creating fork for {upstream_repo}...")
                fork_code, fork_output = run_subprocess(["gh", "repo", "fork", upstream_repo, "--clone=false"], repo)
                if fork_code != 0:
                    return False, f"Fork creation failed: {fork_output}", "Resolve the fork creation error, then rerun publish."
                result["actions"].append("fork created with gh")
                result["attempted_fixes"].append("created fork")
        if preflight.get("origin_url") != target["url"]:
            print("Publish target: updating origin to resolved fork target.")
            print(f"origin before: {preflight.get('origin_url')}")
            print(f"origin after:  {target['url']}")
            changed, output = set_origin_remote_url(repo, target["url"])
            if not changed:
                return False, f"Publish blocked because updating origin to the resolved fork failed: {output}", (
                    f"Set origin manually with `git remote set-url origin {target['url']}` and retry."
                )
            result["actions"].append("origin updated to resolved fork target")
            result["attempted_fixes"].append("origin updated to fork")
            preflight["origin_url"] = target["url"]
            preflight["origin_owner"] = preflight.get("current_user", preflight.get("origin_owner", ""))
            preflight["transport"] = "ssh"
            preflight["requires_fork"] = False
    result["remote_url"] = target["url"]
    result["auth_transport"] = target["transport"]
    return True, "", ""


def parse_head_commit(repo: Path) -> str:
    try:
        code, output = run_subprocess(["git", "rev-parse", "HEAD"], repo)
    except Exception:
        return ""
    return output.strip() if code == 0 else ""


def git_dir_path(repo: Path) -> Path:
    code, output = run_subprocess(["git", "rev-parse", "--git-dir"], repo)
    if code != 0 or not output.strip():
        return repo / ".git"
    git_dir = Path(output.strip())
    return git_dir if git_dir.is_absolute() else (repo / git_dir).resolve()


def detect_git_sequence_state(repo: Path) -> str:
    git_dir = git_dir_path(repo)
    if (git_dir / "MERGE_HEAD").exists():
        return "merge"
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return "cherry_pick"
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return "rebase"
    return "none"


def make_publish_branch_name() -> str:
    return f"fix-agent/{time.strftime('%Y%m%d-%H%M%S')}"


def publish_validated_run(
    repo: Path,
    test_cmd: str,
    attempt_number: int,
    confidence_level: str,
    artifact_dir: Path | None,
    changed_paths: list[str],
    publish_branch: str,
    publish_pr: bool,
    publish_merge: bool,
    publish_merge_local_main: bool,
    publish_message: str,
    target: str,
    blocked_reason: str | None,
    baseline_paths: list[str],
    dry_run_mode: bool,
    publish_current_mode: bool = False,
    validation_state: str = "success",
    force_publish: bool = False,
    auto_stage_safe_paths: bool = True,
    auto_remediate_blockers: bool = True,
    explain_staging: bool = False,
) -> dict:
    result = make_publish_result()
    current_commit = parse_head_commit(repo) if is_git_repo(repo) else ""
    result["publish_scope"] = "current_repo_state" if publish_current_mode else "validated_run"
    result["requested"] = True
    result["validation_state"] = validation_state
    result["validation_commit_match"] = bool(current_commit)
    result["last_validated_commit"] = current_commit
    result["current_commit"] = current_commit
    result["validation_age_seconds"] = 0 if current_commit else -1
    result["forced_publish"] = force_publish
    result["publish_reason"] = "forced" if force_publish and validation_state != "success" else "validated"
    publish_state = load_publish_state(repo)
    result["publish_state"] = publish_state
    result["state_loaded"] = bool(publish_state.get("timestamp"))
    result["transport_locked"] = bool(publish_state.get("ssh_confirmed"))
    result["environment"] = detect_publish_environment()
    result["recommended_command"] = recommended_publish_command(include_pr=publish_pr or publish_merge)
    result["explain_staging"] = explain_staging
    blocker_policy = load_publish_blocker_policy(repo, auto_remediate=auto_remediate_blockers)

    def audit_publish_staging(expected_paths: list[str]) -> dict:
        working_tree = classify_publish_working_tree(repo)
        result["working_tree"] = working_tree
        audit = normalize_publish_working_tree_audit(
            repo,
            working_tree,
            expected_paths,
            publish_current_mode=publish_current_mode,
        )
        decisions, summary, remaining_unstaged, overall_reason = build_publish_file_decisions(
            list(audit.get("entries") or []),
            expected_paths=expected_paths,
            staged_paths=audit["staged_paths"],
            remaining_paths=audit["remaining_paths"],
            auto_staged_paths=result.get("auto_staged_paths") or [],
            auto_stage_entry_state=result.get("_auto_stage_entry_state") or {},
        )
        decision_sets = summarize_publish_decision_sets(decisions, remaining_unstaged)
        unresolved_for_analysis = [
            item
            for item in remaining_unstaged
            if item.get("path") in set(decision_sets.get("safe_stage_candidate_paths") or [])
            or item.get("path") in {str(entry.get("path") or "") for entry in (decision_sets.get("true_blockers") or [])}
        ]
        result["file_decisions"] = decisions
        result["staging_summary"] = summary
        result["remaining_unstaged"] = remaining_unstaged
        result["remaining_unstaged_paths"] = list(decision_sets.get("unresolved_paths") or [])
        result["safe_staged_paths"] = list(decision_sets.get("safe_staged_paths") or [])
        result["ignored_nonblocking_paths"] = list(decision_sets.get("ignored_nonblocking_paths") or [])
        result["safe_stage_candidate_paths"] = list(decision_sets.get("safe_stage_candidate_paths") or [])
        result["true_blockers"] = list(decision_sets.get("true_blockers") or [])
        result["blocker_count"] = int(decision_sets.get("blocker_count") or 0)
        result["publishable_ready"] = bool(decision_sets.get("publishable_ready"))
        analyses = analyze_publish_blockers(repo, unresolved_for_analysis)
        enriched_analyses: list[dict] = []
        for item in analyses:
            enriched = dict(item)
            enriched.update(classify_publish_blocker_remediation(repo, item, blocker_policy))
            enriched_analyses.append(enriched)
        result["blocked_file_analysis"] = enriched_analyses
        result["blocked_analysis_summary"] = summarize_publish_block_analysis(
            result["blocked_file_analysis"],
            rerun_command=result.get("recommended_command") or "./scripts/fixpublish.sh",
        )
        result["blocked_analysis_summary"]["safe_staged_paths"] = list(result.get("safe_staged_paths") or [])
        result["blocked_analysis_summary"]["ignored_nonblocking_paths"] = list(result.get("ignored_nonblocking_paths") or [])
        result["blocked_analysis_summary"]["true_blockers"] = list(result.get("true_blockers") or [])
        result["blocked_analysis_summary"]["blocker_count"] = int(result.get("blocker_count") or 0)
        result["blocked_analysis_summary"]["publishable_ready"] = bool(result.get("publishable_ready"))
        result["remaining_true_blockers"] = list(result.get("true_blockers") or [])
        if not result.get("staging_decision_reason"):
            result["staging_decision_reason"] = overall_reason
        return audit

    def ensure_publish_staging(expected_paths: list[str], *, restore_paths: list[str] | None = None) -> tuple[bool, dict]:
        audit = audit_publish_staging(expected_paths)
        safe_stage_candidates = list(result.get("safe_stage_candidate_paths") or [])
        true_blockers = list(result.get("true_blockers") or [])
        if result.get("publishable_ready"):
            if not result.get("staging_reason"):
                result["staging_reason"] = "all publishable changes already staged"
            result["staging_decision_reason"] = "all publishable changes already staged"
            return True, audit
        safe_paths, blocked_paths = split_publish_auto_stage_paths(audit["remaining_paths"])
        result["remaining_unstaged_paths"] = list(result.get("remaining_unstaged_paths") or [])
        if not auto_stage_safe_paths:
            result["auto_stage_attempted"] = False
            result["auto_stage_result"] = "blocked"
            result["staging_reason"] = "automatic staging disabled by --no-auto-stage"
            result["staging_decision_reason"] = (
                "automatic staging is disabled; manual staging is required for safe publishable files"
                if safe_stage_candidates and not true_blockers
                else "automatic staging is disabled; manual review is required for unstaged publishable files"
            )
            result["next_action"] = format_manual_staging_handoff(
                "Publish blocked because automatic staging is disabled.",
                safe_stage_candidates or list(result.get("remaining_unstaged_paths") or []),
                restore_paths=restore_paths,
            )
            audit_publish_staging(expected_paths)
            return False, audit
        if safe_paths:
            result["auto_stage_attempted"] = True
            entry_index = {str(item.get("path") or ""): item for item in (audit.get("entries") or [])}
            for path in safe_paths:
                source_entry = entry_index.get(path) or {}
                result["_auto_stage_entry_state"][path] = {
                    "tracked": bool(source_entry.get("tracked", True)),
                    "untracked": bool(source_entry.get("untracked")),
                }
            code, output = run_subprocess(["git", "add", "-A", "--", *safe_paths], repo)
            if code != 0:
                result["auto_stage_result"] = "blocked"
                result["staging_reason"] = f"automatic staging failed: {output}"
                result["staging_decision_reason"] = "automatic staging failed; manual review is required"
                result["next_action"] = format_manual_staging_handoff(
                    "Publish blocked because automatic staging failed.",
                    audit["remaining_paths"],
                    restore_paths=restore_paths,
                )
                audit_publish_staging(expected_paths)
                return False, audit
            result["auto_staged_paths"] = sorted(set(list(result.get("auto_staged_paths") or []) + safe_paths))
            audit = audit_publish_staging(expected_paths)
        remediation_attempt_limit = 3
        remediation_attempts = 0
        while remediation_attempts < remediation_attempt_limit:
            true_blockers = list(result.get("true_blockers") or [])
            safe_stage_candidates = list(result.get("safe_stage_candidate_paths") or [])
            if not true_blockers:
                break
            remediation = remediate_publish_blockers(repo, list(result.get("blocked_file_analysis") or []), blocker_policy)
            if remediation.get("result") == "not_needed":
                break
            remediation_attempts += 1
            result["blocker_remediation_attempted"] = bool(result.get("blocker_remediation_attempted")) or bool(remediation.get("attempted"))
            result["auto_removed_paths"] = sorted(set(list(result.get("auto_removed_paths") or []) + list(remediation.get("auto_removed_paths") or [])))
            result["auto_ignored_patterns"] = sorted(set(list(result.get("auto_ignored_patterns") or []) + list(remediation.get("auto_ignored_patterns") or [])))
            previous_result = str(result.get("blocker_remediation_result") or "not_needed")
            current_result = str(remediation.get("result") or "blocked")
            if previous_result == "success" and current_result == "success":
                result["blocker_remediation_result"] = "success"
            elif previous_result in {"partial", "blocked"}:
                result["blocker_remediation_result"] = previous_result
            else:
                result["blocker_remediation_result"] = current_result
            audit = audit_publish_staging(expected_paths)
            result["remaining_true_blockers"] = list(result.get("true_blockers") or [])
            if current_result == "blocked":
                break
        remaining_after = list(result.get("remaining_unstaged_paths") or [])
        safe_stage_candidates = list(result.get("safe_stage_candidate_paths") or [])
        true_blockers = list(result.get("true_blockers") or [])
        result["remaining_unstaged_paths"] = remaining_after
        if true_blockers or safe_stage_candidates:
            result["auto_stage_result"] = "partial" if result.get("auto_staged_paths") else "blocked"
            blocked_reason = "ambiguous or unsafe file requires manual review" if true_blockers else "some publishable files still require manual staging after auto-stage"
            result["staging_reason"] = blocked_reason
            result["staging_decision_reason"] = (
                "one or more files were classified as unknown/artifact and require manual review"
                if true_blockers
                else "one or more publishable files still require manual staging"
            )
            if result.get("blocker_remediation_attempted") and list(result.get("auto_removed_paths") or []):
                result["staging_reason"] = "auto-resolved safe blockers, but one or more ambiguous files still require manual review"
            result["next_action"] = format_manual_staging_handoff(
                (
                    "Publish blocked because one or more true blockers still require manual review."
                    if true_blockers
                    else "Publish blocked because some publishable files still require manual staging."
                ),
                safe_stage_candidates or remaining_after,
                restore_paths=restore_paths,
            )
            audit_publish_staging(expected_paths)
            return False, audit
        if result.get("auto_staged_paths"):
            result["auto_stage_result"] = "success"
            result["staging_reason"] = "auto-staged safe publishable files"
            result["staging_decision_reason"] = "safe publishable files were auto-staged and re-audited successfully"
        if result.get("blocker_remediation_attempted"):
            result["blocker_remediation_result"] = "success"
            if list(result.get("auto_removed_paths") or []):
                result["staging_reason"] = "removed safe temporary artifact blockers and continued publish"
                result["staging_decision_reason"] = "high-confidence safe blockers were auto-resolved and the working tree was re-audited successfully"
        elif not result.get("staging_reason"):
            result["auto_stage_result"] = "not_needed"
            result["staging_reason"] = "all publishable changes already staged"
            result["staging_decision_reason"] = "all publishable changes already staged"
        audit_publish_staging(expected_paths)
        return True, audit

    def finish(status: str, reason: str = "", next_action: str = "") -> dict:
        if result.get("attempted"):
            verification_reason = (result.get("verification") or {}).get("reason") or ""
            if status == "success" and verification_reason:
                status = "failed_verification"
                if not reason:
                    reason = verification_reason
        if reason or next_action:
            set_publish_outcome(result, status, reason, next_action)
        state_payload = {
            "last_target": result.get("target", {}).get("type", ""),
            "last_repo": result.get("target", {}).get("repo", ""),
            "last_transport": result.get("auth_transport", ""),
            "fork_created": bool(result.get("reused_fork")) or bool("fork created with gh" in (result.get("actions") or [])),
            "fork_repo": result.get("target", {}).get("repo", "") if result.get("target", {}).get("type") == "fork" else "",
            "last_success": bool(result.get("final", {}).get("status") == "success"),
            "timestamp": int(time.time()),
            "origin_url": result.get("normalized_origin") or result.get("remote_url", ""),
            "ssh_confirmed": bool(result.get("auth_transport") == "ssh" and result.get("final", {}).get("status") == "success"),
            "last_branch": result.get("branch", ""),
            "last_commit": result.get("commit_sha", ""),
            "last_pr_url": result.get("pr_url", ""),
            "last_success_timestamp": int(time.time()) if result.get("final", {}).get("status") == "success" else 0,
            "last_publish_mode": result.get("publish_scope", ""),
            "last_meaningful_paths": list(result.get("meaningful_paths") or []),
            "last_meaningful_content_fingerprint": result.get("meaningful_content_fingerprint", ""),
            "last_target_repo": result.get("target", {}).get("repo", ""),
            "last_control_path": result.get("control_path", ""),
        }
        save_publish_state(repo, state_payload)
        if not result.get("final_workflow_result"):
            result["final_workflow_result"] = status
        return result

    if target:
        result["remote_url"] = ""
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", "Publish requested, but this workflow only publishes local repos from the controlling machine. Remote publish is not handled.")
    if dry_run_mode:
        return finish("blocked", "Publish blocked because --dry-run skips commit creation and push.")
    if blocked_reason and not force_publish:
        result["control_path"] = "blocked_auth"
        return finish("blocked", f"Publish blocked because the run recorded a blocked state: {blocked_reason}")
    if not is_git_repo(repo):
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", "Publish requested, but the repo is not a git repository.")
    if baseline_paths:
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", "Publish blocked because the working tree contained pre-existing meaningful changes before this run: " + ", ".join(baseline_paths[:10]))

    remotes = parse_remote_names(repo)
    if "origin" not in remotes:
        result["next_action"] = "Configure an `origin` remote before rerunning `--publish`."
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", "Publish blocked because git remote 'origin' is not configured.", result["next_action"])

    current_branch = current_git_branch(repo)
    default_branch = detect_default_branch(repo)
    requested_branch = publish_branch.strip()
    branch_to_push = current_branch
    result["branch"] = branch_to_push
    result["preflight"] = build_publish_preflight(repo, current_branch)
    current_origin_before_normalization = result["preflight"]["origin_url"]
    stored_origin = publish_state.get("origin_url", "")
    if stored_origin and current_origin_before_normalization and stored_origin != current_origin_before_normalization:
        result["state_reset"] = True
        publish_state = {}
        result["publish_state"] = {}
        result["state_loaded"] = False
        result["transport_locked"] = False
    if publish_state.get("ssh_confirmed"):
        result["transport_locked"] = True
    ok, normalization_error = apply_origin_normalization(repo, result, publish_state)
    if not ok:
        result["state_confidence"] = "low"
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", normalization_error, "Set `origin` to a valid GitHub repo URL and retry.")
    result["state_confidence"] = compute_state_confidence(result, publish_state, normalization_ok=ok)
    result["remote_url"] = result["preflight"]["origin_url"] or "(unknown)"
    result["normalized_origin"] = result["preflight"]["origin_url"] or ""
    result["auth_transport"] = result["preflight"]["transport"]
    effective_state = publish_state if result["state_confidence"] == "high" else {}
    result["target"] = resolve_publish_target(result["preflight"], effective_state)
    result["reused_fork"] = result["target"].get("reason") == "reusing persisted fork target"
    result["remote_url"] = result["target"].get("url") or result["remote_url"]
    result["auth_transport"] = result["target"].get("transport") or result["auth_transport"]
    set_publish_final(result, "failed", branch=current_branch, remote=result["remote_url"], pr_url=None)

    if result["target"]["type"] == "blocked":
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", "Publish blocked because origin is missing or could not be parsed as a GitHub repo.", "Configure a valid GitHub `origin` remote and rerun `--publish`.")
    if result["target"]["reason"] == "auth_blocked":
        result["control_path"] = "blocked_auth"
        result["state_confidence"] = "low"
        return finish("blocked", "Publish blocked because GitHub authentication could not be verified during preflight.", "Run `gh auth status` and `gh auth login`, then rerun `--publish`.")
    result["control_path"] = "fork_push" if result["target"]["type"] == "fork" else "direct_origin_push"

    current_head_for_diff = parse_head_commit(repo)
    baseline_commit = ""
    if str(publish_state.get("last_branch") or "").strip() == current_branch:
        baseline_commit = str(publish_state.get("last_commit") or "").strip()
    try:
        publish_changes = classify_publishable_changes(repo, baseline_commit=baseline_commit, current_commit=current_head_for_diff or "HEAD")
    except TypeError:
        publish_changes = classify_publishable_changes(repo)
    result["meaningful_changes_detected"] = bool(publish_changes.get("meaningful_changes_detected"))
    result["meaningful_paths"] = publish_changes.get("meaningful_paths") or []
    result["ignored_changes"] = publish_changes.get("ignored_changes") or []
    result["last_published_commit"] = publish_changes.get("last_published_commit") or ""
    result["current_publish_candidate_commit"] = publish_changes.get("current_commit") or current_head_for_diff or ""
    result["diff_files_detected"] = publish_changes.get("diff_files_detected") or []
    if not result["meaningful_changes_detected"]:
        noop_entries = collect_publish_working_tree_entries(repo)
        decisions, summary, remaining_unstaged, overall_reason = build_publish_file_decisions(
            noop_entries,
            expected_paths=[],
            staged_paths=[],
            remaining_paths=[],
            auto_staged_paths=[],
        )
        result["file_decisions"] = decisions
        result["staging_summary"] = summary
        result["remaining_unstaged"] = remaining_unstaged
        decision_sets = summarize_publish_decision_sets(decisions, remaining_unstaged)
        result["remaining_unstaged_paths"] = list(decision_sets.get("unresolved_paths") or [])
        result["safe_staged_paths"] = list(decision_sets.get("safe_staged_paths") or [])
        result["ignored_nonblocking_paths"] = list(decision_sets.get("ignored_nonblocking_paths") or [])
        result["safe_stage_candidate_paths"] = list(decision_sets.get("safe_stage_candidate_paths") or [])
        result["true_blockers"] = list(decision_sets.get("true_blockers") or [])
        result["blocker_count"] = int(decision_sets.get("blocker_count") or 0)
        result["publishable_ready"] = bool(decision_sets.get("publishable_ready"))
        result["staging_decision_reason"] = overall_reason
        result["control_path"] = "noop"
        result["summary_status"] = "no meaningful changes to publish"
        return finish("noop", "no meaningful changes to publish")
    docs_stage = run_prepublish_docs_stage(repo, test_cmd, result["meaningful_paths"], publish_current_mode=publish_current_mode)
    result["docs_checked_at_publish"] = bool(docs_stage.get("docs_checked_at_publish"))
    result["docs_required"] = bool(docs_stage.get("docs_required"))
    result["docs_updated"] = bool(docs_stage.get("docs_updated"))
    result["docs_refresh_mode"] = str(docs_stage.get("docs_refresh_mode") or "none")
    result["docs_targets"] = list(docs_stage.get("docs_targets") or [])
    result["docs_updated_targets"] = list(docs_stage.get("updated_targets") or [])
    result.update(
        summarize_docs_publish_reporting(
            docs_check_performed=result["docs_checked_at_publish"],
            docs_required=result["docs_required"],
            docs_updated=result["docs_updated"],
            blocked=bool(docs_stage.get("blocked")),
            reason=str(docs_stage.get("reason") or ""),
        )
    )
    if docs_stage.get("blocked"):
        result["control_path"] = "blocked_docs"
        return finish("blocked", str(docs_stage.get("reason") or "docs update blocked publish"))
    if docs_stage.get("docs_updated"):
        try:
            publish_changes = classify_publishable_changes(repo, baseline_commit=baseline_commit, current_commit=current_head_for_diff or "HEAD")
        except TypeError:
            publish_changes = classify_publishable_changes(repo)
        result["meaningful_changes_detected"] = bool(publish_changes.get("meaningful_changes_detected"))
        result["meaningful_paths"] = publish_changes.get("meaningful_paths") or []
        result["ignored_changes"] = publish_changes.get("ignored_changes") or []
        result["last_published_commit"] = publish_changes.get("last_published_commit") or ""
        result["current_publish_candidate_commit"] = publish_changes.get("current_commit") or current_head_for_diff or ""
        result["diff_files_detected"] = publish_changes.get("diff_files_detected") or []
        if not publish_current_mode:
            changed_paths = sorted(set(changed_paths) | set(docs_stage.get("updated_targets") or []))
    result["meaningful_content_fingerprint"] = compute_meaningful_content_fingerprint(repo, publish_changes)
    result["triggered"] = True

    branch_is_default = current_branch in {"main", "master", default_branch}
    if requested_branch:
        if current_branch != requested_branch:
            code, output = run_subprocess(["git", "checkout", "-b", requested_branch], repo)
            if code != 0:
                code, output = run_subprocess(["git", "checkout", requested_branch], repo)
                if code != 0:
                    return finish("blocked", f"Publish blocked because branch setup failed: {output}")
        branch_to_push = requested_branch
        result["branch"] = branch_to_push
    elif branch_is_default:
        auto_branch = make_publish_branch_name()
        if not sys.stdin.isatty():
            code, output = run_subprocess(["git", "checkout", "-b", auto_branch], repo)
            if code != 0:
                return finish(
                    "blocked",
                    (
                        f"Publish blocked because automatic safe branch creation from default branch "
                        f"'{current_branch}' failed in non-interactive mode: {output}"
                    ),
                    "Resolve branch creation failure or pass --publish-branch explicitly.",
                )
            result["actions"].append("auto-created publish branch from default branch")
            branch_to_push = auto_branch
            result["branch"] = branch_to_push
        else:
            if not prompt_yes_no(
                f"Publish is requested from default branch '{current_branch}'. Create and switch to '{auto_branch}'?",
                default=True,
            ):
                return finish("blocked", "Publish cancelled because branch creation was not confirmed.")
            code, output = run_subprocess(["git", "checkout", "-b", auto_branch], repo)
            if code != 0:
                return finish("blocked", f"Publish blocked because safe branch creation failed: {output}")
            branch_to_push = auto_branch
            result["branch"] = branch_to_push

    result["preflight"]["branch"] = branch_to_push
    ok, preflight_fix_output, preflight_next_action = prepare_publish_target(repo, result)
    if not ok:
        return finish("blocked", preflight_fix_output, preflight_next_action)
    result["remote_url"] = result["target"]["url"] or result["remote_url"]
    result["auth_transport"] = result["target"]["transport"] or result["auth_transport"]
    set_publish_final(result, "failed", branch=branch_to_push, remote=result["remote_url"], pr_url=None)

    current_paths = publish_meaningful_changed_paths(repo)
    working_tree = classify_publish_working_tree(repo)
    result["working_tree"] = working_tree
    if not publish_current_mode:
        unrelated_paths = sorted(set(current_paths) - set(changed_paths))
        if unrelated_paths:
            return finish("blocked", "Publish blocked because the working tree contains unrelated changes: " + ", ".join(unrelated_paths[:10]))
    head_sha = current_head_for_diff or parse_head_commit(repo)
    result["fingerprint"] = {
        "matched_previous_success": False,
        "reason": "",
        "commit": head_sha,
        "branch": branch_to_push,
        "target_repo": result["target"].get("repo", ""),
    }
    same_target = publish_state.get("last_target_repo") == result["target"].get("repo", "")
    same_publish_mode = publish_state.get("last_publish_mode") == result.get("publish_scope")
    clean_exact_match = (
        bool(publish_state.get("last_success"))
        and working_tree.get("clean")
        and bool(publish_state.get("last_commit"))
        and publish_state.get("last_commit") == head_sha
        and same_target
        and same_publish_mode
    )
    meaningful_exact_match = (
        bool(publish_state.get("last_success"))
        and bool(publish_state.get("last_commit"))
        and publish_state.get("last_commit") == head_sha
        and bool(result.get("meaningful_content_fingerprint"))
        and bool(publish_state.get("last_meaningful_content_fingerprint"))
        and publish_state.get("last_meaningful_content_fingerprint") == result.get("meaningful_content_fingerprint")
        and same_target
        and same_publish_mode
    )
    if clean_exact_match or meaningful_exact_match:
        result["control_path"] = "noop"
        result["fingerprint"]["matched_previous_success"] = True
        if clean_exact_match:
            result["fingerprint"]["reason"] = "previous_commit matched; current tree clean; target and publish mode matched"
        else:
            result["fingerprint"]["reason"] = "current meaningful content matched stored successful publish fingerprint; ignored-only changes were excluded"
        result["summary_status"] = "matched previous successful publish fingerprint"
        result["previous_publish_branch"] = str(publish_state.get("last_branch") or "")
        result["previous_pr_url"] = str(publish_state.get("last_pr_url") or "")
        result["previous_commit"] = str(publish_state.get("last_commit") or "")
        return finish("noop", "matched previous successful publish fingerprint")
    if publish_state.get("last_success") and same_target and same_publish_mode:
        result["fingerprint"]["reason"] = "fingerprint mismatch due to new meaningful changes"
    already_pushed, local_head = branch_already_up_to_date(repo, branch_to_push, result["target"]["url"] or "origin")
    publish_existing_commit = (
        publish_current_mode
        and bool(working_tree.get("clean"))
        and bool(result.get("diff_files_detected"))
    )
    if publish_current_mode and working_tree["clean"] and not publish_existing_commit:
        commit_ref = local_head or head_sha
        mark_publish_noop(result, "no changes to publish", branch_to_push, result["remote_url"], commit_ref)
        return finish("noop")
    if not publish_current_mode and (not changed_paths or not current_paths):
        commit_ref = local_head or head_sha
        if already_pushed:
            mark_publish_noop(result, "Publish noop: branch already up to date on origin.", branch_to_push, result["remote_url"], commit_ref)
            return finish("noop")
        mark_publish_noop(result, "Publish noop: nothing to commit.", branch_to_push, result["remote_url"], commit_ref)
        return finish("noop")

    commit_sha = ""
    if publish_current_mode:
        if publish_existing_commit:
            commit_sha = head_sha or parse_head_commit(repo)
            result["commit_sha"] = commit_sha
            result["current_publish_candidate_commit"] = commit_sha
            result["summary_status"] = "publishing existing committed repo state"
            set_publish_final(result, "failed", branch=branch_to_push, commit=commit_sha, remote=result["remote_url"], pr_url=None)
        else:
            docs_updated_targets = list(result.get("docs_updated_targets") or [])
            if docs_updated_targets:
                code, output = run_subprocess(["git", "add", "-A", "--", *docs_updated_targets], repo)
                if code != 0:
                    return finish("blocked", f"Publish blocked because staging docs updates failed: {output}")
                result["actions"].append("staged docs updates")
            ignored_changes = list(result.get("ignored_changes") or [])
            staging_ok, audit = ensure_publish_staging(result["meaningful_paths"], restore_paths=ignored_changes)
            staged_paths = audit["staged_paths"]
            if not staging_ok:
                return finish(
                    "blocked",
                    "Publish blocked because publishable changes remained unstaged after staging: "
                    + ", ".join((result.get("remaining_unstaged_paths") or audit["remaining_paths"])[:10]),
                    result.get("next_action") or "",
                )
            if ignored_changes:
                result["actions"].append("excluded internal files left unstaged")
            if not staged_paths:
                commit_ref = local_head or parse_head_commit(repo)
                mark_publish_noop(result, "no changes to publish", branch_to_push, result["remote_url"], commit_ref)
                return finish("noop")
            result["summary_status"] = f"staged {len(staged_paths)} publishable file(s)"
    else:
        code, output = run_subprocess(["git", "add", "-A", "--", *changed_paths], repo)
        if code != 0:
            return finish("blocked", f"Publish blocked because staging failed: {output}")

        staging_ok, audit = ensure_publish_staging(changed_paths)
        staged_paths = audit["staged_paths"]
        unrelated_staged = sorted(set(staged_paths) - set(changed_paths))
        if unrelated_staged:
            return finish("blocked", "Publish blocked because staging picked up unrelated files: " + ", ".join(unrelated_staged[:10]))
        missing_staged = sorted(set(changed_paths) - set(staged_paths))
        if (result.get("remaining_unstaged_paths") or missing_staged) and not staging_ok:
            blocked_paths = list(result.get("remaining_unstaged_paths") or missing_staged)
            return finish(
                "blocked",
                "Publish blocked because requested publishable changes were not fully staged: " + ", ".join(blocked_paths[:10]),
                result.get("next_action") or "",
            )
        if not staged_paths:
            commit_ref = local_head or parse_head_commit(repo)
            if already_pushed:
                mark_publish_noop(result, "Publish noop: branch already up to date on origin.", branch_to_push, result["remote_url"], commit_ref)
                return finish("noop")
            mark_publish_noop(result, "Publish noop: nothing to commit.", branch_to_push, result["remote_url"], commit_ref)
            return finish("noop")
        result["summary_status"] = f"staged {len(staged_paths)} publishable file(s)"

    if not publish_existing_commit:
        commit_message = publish_message.strip() or "fix(agent): apply validated repair"
        if result["environment"].get("interactive"):
            if not prompt_yes_no(
                f"Publish commit to branch '{branch_to_push}' with message '{commit_message}'?",
                default=True,
            ):
                return finish("blocked", "Publish cancelled before commit.")

        code, output = run_subprocess(["git", "commit", "-m", commit_message], repo)
        if code != 0:
            if "nothing to commit" in output.lower():
                already_pushed, local_head = branch_already_up_to_date(repo, branch_to_push, result["target"]["url"] or "origin")
                commit_ref = local_head or parse_head_commit(repo)
                if already_pushed:
                    mark_publish_noop(result, "Publish noop: branch already up to date on origin.", branch_to_push, result["remote_url"], commit_ref)
                    return finish("noop")
                mark_publish_noop(result, "Publish noop: nothing to commit.", branch_to_push, result["remote_url"], commit_ref)
                return finish("noop")
            return finish("blocked", f"Publish blocked because commit failed: {output}")

        commit_sha = parse_head_commit(repo)
        result["commit_sha"] = commit_sha
        result["current_publish_candidate_commit"] = commit_sha
        set_publish_final(result, "failed", branch=branch_to_push, commit=commit_sha, remote=result["remote_url"], pr_url=None)

    base_branch, existing_pr_url = resolve_prepublish_base_branch(repo, branch_to_push, default_branch)
    result["base_branch"] = base_branch
    alignment = align_branch_with_base_before_publish(
        repo,
        branch=branch_to_push,
        base_branch=base_branch,
        validation_command=test_cmd,
        no_auto_conflict_resolution_after_sync=CURRENT_NO_AUTO_CONFLICT_RESOLUTION_AFTER_SYNC,
    )
    result["prepublish_base_alignment_attempted"] = bool(alignment.get("prepublish_base_alignment_attempted"))
    result["branch_diverged"] = bool(alignment.get("branch_diverged"))
    result["alignment_needed"] = bool(alignment.get("alignment_needed"))
    result["alignment_result"] = str(alignment.get("alignment_result") or "not_needed")
    result["alignment_changed_commit"] = bool(alignment.get("alignment_changed_commit"))
    result["validation_rerun_after_alignment"] = bool(alignment.get("validation_rerun_after_alignment"))
    result["alignment_block_reason"] = str(alignment.get("alignment_block_reason") or "")
    if alignment.get("merge_conflict_result"):
        result["merge_conflict_result"] = alignment.get("merge_conflict_result")
    if existing_pr_url:
        result["pr_url"] = existing_pr_url
    if result["alignment_result"] == "blocked":
        result["control_path"] = "blocked_alignment"
        return finish("blocked", result["alignment_block_reason"] or "pre-publish base alignment blocked publish")
    if result["alignment_changed_commit"]:
        commit_sha = parse_head_commit(repo)
        result["commit_sha"] = commit_sha
        result["current_publish_candidate_commit"] = commit_sha
        result["validation_state"] = "success"
        result["validation_commit_match"] = True
        result["last_validated_commit"] = commit_sha
        result["current_commit"] = commit_sha
        result["validation_age_seconds"] = 0
        result["validation_reused"] = False
        result["auto_revalidated"] = False
        result["auto_revalidation_result"] = "not_needed"
        set_publish_final(result, "failed", branch=branch_to_push, commit=commit_sha, remote=result["remote_url"], pr_url=result.get("pr_url") or None)
        result["actions"].append("aligned publish branch with base")

    if result["environment"].get("interactive"):
        if not prompt_yes_no(
            f"Push branch '{branch_to_push}' to origin?",
            default=True,
        ):
            return finish("blocked", "Publish stopped after commit because push was not confirmed.")

    if already_pushed and publish_existing_commit:
        result["actions"].append("reused existing pushed branch")
        result["attempted"] = True
    else:
        code, output = run_subprocess(["git", "push", "-u", "origin", branch_to_push], repo)
        if code != 0:
            lowered = (output or "").lower()
            if any(token in lowered for token in ["authentication failed", "permission denied", "403", "could not read username"]):
                return finish("failed_auth", f"Publish blocked because push failed with an authentication error: {output}", "Verify GitHub auth for the resolved target and retry.")
            if "repository not found" in lowered and result["target"]["type"] == "fork":
                return finish("missing_fork", f"Publish blocked because the resolved fork target was not found: {output}", f"Create or verify fork `{result['target']['repo']}`, then set origin with `git remote set-url origin {result['target']['url']}` and retry.")
            return finish("failed", f"Publish blocked because push failed: {output}", "Inspect the resolved target URL, auth, and branch permissions before retrying.")
        result["attempted"] = True
    update_post_publish_reporting(repo, result, branch_to_push)
    current_working_tree = result.get("working_tree") or {}
    result["working_tree_publishable_paths"] = list(current_working_tree.get("publishable_paths") or [])
    result["working_tree_internal_paths"] = list(current_working_tree.get("internal_paths") or [])
    result["working_tree_artifact_paths"] = list(current_working_tree.get("artifact_paths") or [])
    result["working_tree_other_paths"] = list(current_working_tree.get("other_paths") or [])

    pr_url = ""
    want_pr = publish_pr or publish_merge
    result["pr_requested"] = publish_pr
    if want_pr:
        if not result["preflight"]["gh_available"]:
            result["pr_status"] = "blocked"
            result["pr_reason"] = "PR creation could not proceed because GitHub CLI is not installed."
            result["reason"] = f"Publish succeeded, but {result['pr_reason']}"
        elif not result["preflight"]["gh_auth"]:
            result["pr_status"] = "blocked"
            result["pr_reason"] = "PR creation could not proceed because GitHub CLI is not authenticated."
            result["reason"] = f"Publish succeeded, but {result['pr_reason']}"
        else:
            existing_pr = detect_existing_pr(repo, branch_to_push)
            if existing_pr:
                result["pr_already_exists"] = True
                result["pr_created_or_reused"] = True
                result["pr_status"] = "reused"
                pr_url = existing_pr
                result["actions"].append("reused existing pr")
            else:
                title = commit_message
                body = "\n".join(
                    [
                        f"- test command: `{test_cmd}`",
                        f"- attempts: {attempt_number}",
                        f"- confidence: {confidence_level}",
                        f"- artifacts: `{artifact_dir}`" if artifact_dir else "- artifacts: n/a",
                    ]
                )
                code, pr_output = run_subprocess(
                    ["gh", "pr", "create", "--title", title, "--body", body],
                    repo,
                )
                if code == 0:
                    pr_url = extract_github_pr_url(pr_output)
                    result["pr_created_or_reused"] = bool(pr_url)
                    result["pr_status"] = "created" if pr_url else "blocked"
                    if not pr_url:
                        result["pr_reason"] = "PR creation command succeeded but no PR URL was returned."
                    if pr_url:
                        result["actions"].append("created pr")
                else:
                    result["pr_status"] = "failed"
                    result["pr_reason"] = f"PR creation could not proceed: {pr_output}"
                    result["reason"] = f"Publish succeeded, but {result['pr_reason']}"
        if not pr_url and not result["pr_reason"] and result["pr_status"] in {"blocked", "failed"}:
            result["pr_reason"] = "PR requested but no PR URL is available."
    else:
        result["pr_status"] = "not_requested"

    if publish_merge:
        result["pr_url"] = pr_url
        merged, merge_reason = merge_published_pr(repo, result)
        if merged:
            result["pr_merged"] = True
        elif merge_reason:
            result["reason"] = merge_reason

    if publish_merge_local_main and result.get("pr_merged"):
        synced, sync_reason = sync_local_main_after_merge(repo)
        if synced:
            result["local_main_synced"] = True
            result["actions"].append("local main synced")
        elif sync_reason:
            result["reason"] = sync_reason

    if publish_pr and not pr_url and not result.get("pr_reason"):
        result["pr_reason"] = "PR requested but no PR URL is available."
        if result["pr_status"] == "not_requested":
            result["pr_status"] = "failed"

    pr_url = normalize_publish_pr_fields(
        repo,
        result,
        branch=branch_to_push,
        publish_pr_requested=bool(want_pr),
        publish_state=publish_state,
        hinted_pr_url=pr_url,
    )

    if pr_url:
        mergeability = resolve_pr_mergeability(repo, pr_url)
        result["pr_mergeable"] = mergeability.get("pr_mergeable") or "unknown"
        result["pr_conflicts_detected"] = bool(mergeability.get("pr_conflicts_detected"))
        result["pr_mergeability_reason"] = str(mergeability.get("pr_mergeability_reason") or "")
        result["pr_mergeability_source"] = str(mergeability.get("pr_mergeability_source") or "github")
        result["pr_mergeable_final"] = mergeability.get("pr_mergeable_final") or result["pr_mergeable"]
        result["pr_conflicts_detected_final"] = bool(mergeability.get("pr_conflicts_detected_final"))
        if result["pr_conflicts_detected_final"]:
            repair = repair_pr_mergeability(repo, pr_url, validation_command=test_cmd)
            result["pr_mergeability_repair_attempted"] = bool(repair.get("attempted"))
            result["pr_mergeability_repair_result"] = str(repair.get("result") or "blocked")
            refreshed = repair.get("mergeability") or {}
            if refreshed:
                result["pr_mergeable"] = refreshed.get("pr_mergeable") or result["pr_mergeable"]
                result["pr_conflicts_detected"] = bool(refreshed.get("pr_conflicts_detected"))
                result["pr_mergeability_reason"] = str(refreshed.get("pr_mergeability_reason") or result["pr_mergeability_reason"] or "")
                result["pr_mergeability_source"] = str(refreshed.get("pr_mergeability_source") or result["pr_mergeability_source"] or "github")
                result["pr_mergeable_final"] = refreshed.get("pr_mergeable_final") or result["pr_mergeable_final"] or result["pr_mergeable"]
                result["pr_conflicts_detected_final"] = bool(refreshed.get("pr_conflicts_detected_final"))
            if repair.get("merge_conflict_result"):
                result["merge_conflict_result"] = repair.get("merge_conflict_result")
            if repair.get("result") != "success":
                result["final_workflow_result"] = "blocked"
                if repair.get("reason"):
                    result["reason"] = str(repair.get("reason"))
            else:
                result["actions"].append("repaired pr mergeability")
        else:
            result["pr_mergeability_repair_result"] = "not_needed"

    set_publish_final(result, "success", branch=branch_to_push, commit=commit_sha, remote=result["remote_url"], pr_url=pr_url or None)
    return finish("success")


def publish_current_repo_state(
    repo: Path,
    publish_branch: str,
    publish_pr: bool,
    publish_merge: bool,
    publish_merge_local_main: bool,
    publish_message: str,
    target: str,
    dry_run_mode: bool,
    validation_state: str = "success",
    validation_detail: str = "",
    force_publish: bool = False,
    validation_commit_match: bool = False,
    fingerprint_match: bool = False,
    last_validated_commit: str = "",
    current_commit: str = "",
    validation_age_seconds: int = -1,
    auto_revalidated: bool = False,
    validation_reused: bool = False,
    auto_revalidation_result: str = "not_needed",
    auto_stage_safe_paths: bool = True,
    auto_remediate_blockers: bool = True,
    explain_staging: bool = False,
) -> dict:
    if validation_state != "success" and not force_publish:
        result = make_publish_result()
        result["publish_scope"] = "current_repo_state"
        result["explain_staging"] = explain_staging
        result["requested"] = True
        result["validation_state"] = validation_state
        result["validation_commit_match"] = validation_commit_match
        result["fingerprint_match"] = fingerprint_match
        result["last_validated_commit"] = last_validated_commit
        result["current_commit"] = current_commit
        result["validation_age_seconds"] = validation_age_seconds
        result["auto_revalidated"] = auto_revalidated
        result["validation_reused"] = validation_reused
        result["auto_revalidation_result"] = auto_revalidation_result
        result["forced_publish"] = False
        result["publish_reason"] = "blocked_by_validation"
        result["reason"] = validation_detail or f"publish blocked because validation_result={validation_state}; use --force-publish to override"
        result["control_path"] = "blocked_validation"
        set_publish_final(result, "blocked")
        return result
    result = publish_validated_run(
        repo,
        "n/a (publish current repo state)",
        0,
        "n/a",
        None,
        [],
        publish_branch,
        publish_pr,
        publish_merge,
        publish_merge_local_main,
        publish_message.strip() or "chore: publish current repo state",
        target,
        None,
        [],
        dry_run_mode,
        True,
        validation_state=validation_state,
        force_publish=force_publish,
        auto_stage_safe_paths=auto_stage_safe_paths,
        auto_remediate_blockers=auto_remediate_blockers,
        explain_staging=explain_staging,
    )
    result["validation_commit_match"] = validation_commit_match
    result["fingerprint_match"] = fingerprint_match
    result["last_validated_commit"] = last_validated_commit
    result["current_commit"] = current_commit
    result["validation_age_seconds"] = validation_age_seconds
    result["auto_revalidated"] = auto_revalidated
    result["validation_reused"] = validation_reused
    result["auto_revalidation_result"] = auto_revalidation_result
    if auto_revalidated and validation_state == "success":
        result["publish_reason"] = "validated_after_revalidation"
    elif validation_reused and validation_state == "success" and result.get("fingerprint_match"):
        result["publish_reason"] = "validated_reused_fingerprint"
    elif validation_reused and validation_state == "success" and not validation_commit_match:
        result["publish_reason"] = "validated_reused_noop"
    elif validation_reused and validation_state == "success":
        result["publish_reason"] = "validated"
    result["recommended_command"] = recommended_publish_current_command(include_pr=publish_pr or publish_merge)
    return result


def run_post_success_publish(
    repo: Path,
    test_cmd: str,
    attempt_number: int,
    confidence_level: str,
    artifact_dir: Path | None,
    changed_paths: list[str],
    publish_branch: str,
    publish_pr: bool,
    publish_merge: bool,
    publish_merge_local_main: bool,
    publish_message: str,
    target: str,
    blocked_reason: str | None,
    baseline_paths: list[str],
    dry_run_mode: bool,
    publish_mode: str,
    validation_succeeded: bool,
    publish_requested: bool,
    force_publish: bool = False,
    auto_stage_safe_paths: bool = True,
    auto_remediate_blockers: bool = True,
    explain_staging: bool = False,
) -> dict:
    validation_result = "success" if validation_succeeded else ("blocked" if blocked_reason else "failed")
    summary = {
        "validation_state": validation_result,
        "validation_result": validation_result,
        "validation_commit_match": validation_result == "success",
        "last_validated_commit": parse_head_commit(repo) if is_git_repo(repo) else "",
        "current_commit": parse_head_commit(repo) if is_git_repo(repo) else "",
        "validation_age_seconds": 0 if is_git_repo(repo) else -1,
        "auto_revalidated": False,
        "validation_reused": validation_result == "success",
        "auto_revalidation_result": "not_needed",
        "publish_requested": False,
        "publish_triggered": False,
        "publish_mode": publish_mode,
        "pr_created_or_reused": False,
        "pr_merged": False,
        "local_main_synced": False,
        "publish_result": "not_requested",
        "publish_result_detail": None,
        "final_workflow_result": "",
        "publish_reason": "",
        "publish_detail_reason": "",
        "auto_stage_attempted": False,
        "auto_stage_result": "not_needed",
        "auto_staged_paths": [],
        "remaining_unstaged_paths": [],
        "remaining_unstaged": [],
        "blocked_file_analysis": [],
        "blocked_analysis_summary": {},
        "file_decisions": [],
        "staging_summary": {
            "auto_staged": 0,
            "ignored": 0,
            "blocked": 0,
        },
        "staging_decision_reason": "",
        "staging_reason": "",
        "explain_staging": explain_staging,
        "meaningful_changes_detected": False,
        "meaningful_paths": [],
        "ignored_changes": [],
        "docs_checked_at_publish": False,
        "docs_check_performed": False,
        "docs_status": "up_to_date",
        "docs_reason": "documentation check not performed",
        "docs_required": False,
        "docs_updated": False,
        "docs_refresh_mode": "none",
        "docs_targets": [],
        "previous_publish_branch": "",
        "previous_pr_url": "",
        "previous_commit": "",
        "pr_mergeable": "unknown",
        "pr_conflicts_detected": False,
        "pr_mergeability_reason": "",
        "pr_mergeability_repair_attempted": False,
        "pr_mergeability_repair_result": "not_needed",
    }
    if publish_mode == "current-repo-state":
        summary["publish_requested"] = True
        publish_result = publish_current_repo_state(
            repo,
            publish_branch,
            publish_pr,
            publish_merge,
            publish_merge_local_main,
            publish_message,
            target,
            dry_run_mode,
            validation_state=validation_result,
            validation_detail=(blocked_reason or ""),
            force_publish=force_publish,
            validation_commit_match=bool(summary.get("validation_commit_match")),
            fingerprint_match=bool(summary.get("fingerprint_match")),
            last_validated_commit=str(summary.get("last_validated_commit") or ""),
            current_commit=str(summary.get("current_commit") or ""),
            validation_age_seconds=int(summary.get("validation_age_seconds", -1)),
            auto_revalidated=bool(summary.get("auto_revalidated")),
            validation_reused=bool(summary.get("validation_reused")),
            auto_revalidation_result=str(summary.get("auto_revalidation_result") or "not_needed"),
            auto_stage_safe_paths=auto_stage_safe_paths,
            auto_remediate_blockers=auto_remediate_blockers,
            explain_staging=explain_staging,
        )
        summary["publish_triggered"] = bool(publish_result.get("triggered"))
        summary["publish_result_detail"] = publish_result
    elif (validation_succeeded or force_publish) and publish_requested:
        summary["publish_requested"] = True
        publish_result = publish_validated_run(
            repo,
            test_cmd,
            attempt_number,
            confidence_level,
            artifact_dir,
            changed_paths,
            publish_branch,
            publish_pr,
            publish_merge,
            publish_merge_local_main,
            publish_message,
            target,
            blocked_reason,
            baseline_paths,
            dry_run_mode,
            validation_state=validation_result,
            force_publish=force_publish,
            auto_stage_safe_paths=auto_stage_safe_paths,
            auto_remediate_blockers=auto_remediate_blockers,
            explain_staging=explain_staging,
        )
        summary["publish_triggered"] = bool(publish_result.get("triggered"))
        summary["publish_result_detail"] = publish_result
    elif publish_requested and not validation_succeeded:
        summary["publish_requested"] = True
        summary["publish_result"] = "blocked"
        summary["publish_reason"] = "blocked_by_validation"
        summary["publish_detail_reason"] = f"publish blocked because validation_result={validation_result}; use --force-publish to override"
    publish_result = summary.get("publish_result_detail") or {}
    publish_status = str((publish_result.get("final") or {}).get("status") or "").strip()
    if summary["publish_requested"] and validation_succeeded and not summary["publish_triggered"] and publish_status != "noop":
        summary["publish_result"] = "failed"
        summary["publish_reason"] = "validated"
        summary["publish_detail_reason"] = "publish requested after validation success, but publish flow did not run"
    elif publish_result:
        summary["publish_result"] = summarize_publish_result(summary)
    if publish_result:
        summary["validation_state"] = str(publish_result.get("validation_state") or validation_result)
        summary["validation_commit_match"] = bool(publish_result.get("validation_commit_match"))
        summary["last_validated_commit"] = str(publish_result.get("last_validated_commit") or summary.get("last_validated_commit") or "")
        summary["current_commit"] = str(publish_result.get("current_commit") or summary.get("current_commit") or "")
        summary["validation_age_seconds"] = int(publish_result.get("validation_age_seconds", summary.get("validation_age_seconds", -1)))
        summary["auto_revalidated"] = bool(publish_result.get("auto_revalidated"))
        summary["validation_reused"] = bool(publish_result.get("validation_reused"))
        summary["auto_revalidation_result"] = str(publish_result.get("auto_revalidation_result") or summary.get("auto_revalidation_result") or "not_needed")
    if not summary["publish_reason"]:
        summary["publish_reason"] = str(publish_result.get("publish_reason") or "")
    if not summary["publish_detail_reason"]:
        summary["publish_detail_reason"] = (
            publish_result.get("reason")
            or (publish_result.get("verification") or {}).get("reason")
            or publish_result.get("pr_reason")
            or ""
        )
    docs_reporting = summarize_docs_publish_reporting(
        docs_check_performed=bool(
            publish_result.get("docs_check_performed", publish_result.get("docs_checked_at_publish"))
        ),
        docs_required=bool(publish_result.get("docs_required")),
        docs_updated=bool(publish_result.get("docs_updated")),
        blocked=bool(
            publish_result.get("control_path") == "blocked_docs"
            or (
                bool(publish_result.get("docs_required"))
                and not bool(publish_result.get("docs_updated"))
                and bool(publish_result.get("reason"))
            )
        ),
        reason=str(publish_result.get("docs_reason") or publish_result.get("reason") or ""),
    )
    summary["pr_created_or_reused"] = bool(publish_result.get("pr_created_or_reused") or publish_result.get("pr_already_exists"))
    summary["pr_url"] = publish_result.get("pr_url") or ""
    summary["pr_summary"] = publish_result.get("pr_summary") or ""
    summary["pr_merged"] = bool(publish_result.get("pr_merged"))
    summary["local_main_synced"] = bool(publish_result.get("local_main_synced"))
    summary["meaningful_changes_detected"] = bool(publish_result.get("meaningful_changes_detected"))
    summary["meaningful_paths"] = publish_result.get("meaningful_paths") or []
    summary["ignored_changes"] = publish_result.get("ignored_changes") or []
    summary["auto_stage_attempted"] = bool(publish_result.get("auto_stage_attempted"))
    summary["auto_stage_result"] = str(publish_result.get("auto_stage_result") or "not_needed")
    summary["auto_staged_paths"] = publish_result.get("auto_staged_paths") or []
    summary["safe_staged_paths"] = publish_result.get("safe_staged_paths") or []
    summary["ignored_nonblocking_paths"] = publish_result.get("ignored_nonblocking_paths") or []
    summary["safe_stage_candidate_paths"] = publish_result.get("safe_stage_candidate_paths") or []
    summary["true_blockers"] = publish_result.get("true_blockers") or []
    summary["blocker_count"] = int(publish_result.get("blocker_count") or 0)
    summary["publishable_ready"] = bool(publish_result.get("publishable_ready"))
    summary["blocker_remediation_attempted"] = bool(publish_result.get("blocker_remediation_attempted"))
    summary["blocker_remediation_result"] = str(publish_result.get("blocker_remediation_result") or "not_needed")
    summary["auto_removed_paths"] = publish_result.get("auto_removed_paths") or []
    summary["auto_ignored_patterns"] = publish_result.get("auto_ignored_patterns") or []
    summary["remaining_true_blockers"] = publish_result.get("remaining_true_blockers") or []
    summary["remaining_unstaged_paths"] = publish_result.get("remaining_unstaged_paths") or []
    summary["remaining_unstaged"] = publish_result.get("remaining_unstaged") or []
    summary["working_tree_clean"] = bool(publish_result.get("working_tree_clean", True))
    summary["working_tree_publishable_paths"] = publish_result.get("working_tree_publishable_paths") or []
    summary["working_tree_internal_paths"] = publish_result.get("working_tree_internal_paths") or []
    summary["working_tree_artifact_paths"] = publish_result.get("working_tree_artifact_paths") or []
    summary["working_tree_other_paths"] = publish_result.get("working_tree_other_paths") or []
    summary["blocked_file_analysis"] = publish_result.get("blocked_file_analysis") or []
    summary["blocked_analysis_summary"] = publish_result.get("blocked_analysis_summary") or {}
    summary["file_decisions"] = publish_result.get("file_decisions") or []
    summary["staging_summary"] = publish_result.get("staging_summary") or {"auto_staged": 0, "ignored": 0, "blocked": 0}
    summary["staging_decision_reason"] = str(publish_result.get("staging_decision_reason") or "")
    summary["staging_reason"] = str(publish_result.get("staging_reason") or "")
    summary["last_published_commit"] = publish_result.get("last_published_commit") or ""
    summary["current_publish_candidate_commit"] = publish_result.get("current_publish_candidate_commit") or ""
    summary["diff_files_detected"] = publish_result.get("diff_files_detected") or []
    summary["docs_checked_at_publish"] = bool(publish_result.get("docs_checked_at_publish"))
    summary["docs_check_performed"] = bool(docs_reporting.get("docs_check_performed"))
    summary["docs_status"] = str(docs_reporting.get("docs_status") or "up_to_date")
    summary["docs_reason"] = str(docs_reporting.get("docs_reason") or "documentation check not performed")
    summary["docs_required"] = bool(publish_result.get("docs_required"))
    summary["docs_updated"] = bool(publish_result.get("docs_updated"))
    summary["docs_refresh_mode"] = str(publish_result.get("docs_refresh_mode") or "none")
    summary["docs_targets"] = publish_result.get("docs_targets") or []
    summary["previous_publish_branch"] = publish_result.get("previous_publish_branch") or ""
    summary["previous_pr_url"] = publish_result.get("previous_pr_url") or ""
    summary["previous_commit"] = publish_result.get("previous_commit") or ""
    summary["base_branch"] = publish_result.get("base_branch") or ""
    summary["prepublish_base_alignment_attempted"] = bool(publish_result.get("prepublish_base_alignment_attempted"))
    summary["branch_diverged"] = bool(publish_result.get("branch_diverged"))
    summary["alignment_needed"] = bool(publish_result.get("alignment_needed"))
    summary["alignment_result"] = publish_result.get("alignment_result") or "not_needed"
    summary["alignment_changed_commit"] = bool(publish_result.get("alignment_changed_commit"))
    summary["validation_rerun_after_alignment"] = bool(publish_result.get("validation_rerun_after_alignment"))
    summary["alignment_block_reason"] = publish_result.get("alignment_block_reason") or ""
    summary["final_workflow_result"] = publish_result.get("final_workflow_result") or summary.get("publish_result") or ""
    summary["pr_mergeable"] = publish_result.get("pr_mergeable") or "unknown"
    summary["pr_conflicts_detected"] = bool(publish_result.get("pr_conflicts_detected"))
    summary["pr_mergeability_reason"] = publish_result.get("pr_mergeability_reason") or ""
    summary["pr_mergeability_source"] = publish_result.get("pr_mergeability_source") or "github"
    summary["pr_mergeable_final"] = publish_result.get("pr_mergeable_final") or summary["pr_mergeable"]
    summary["pr_conflicts_detected_final"] = bool(publish_result.get("pr_conflicts_detected_final"))
    summary["pr_mergeability_repair_attempted"] = bool(publish_result.get("pr_mergeability_repair_attempted"))
    summary["pr_mergeability_repair_result"] = publish_result.get("pr_mergeability_repair_result") or "not_needed"
    return summary


def print_post_success_publish_summary(summary: dict) -> None:
    docs_reporting = summarize_docs_publish_reporting(
        docs_check_performed=bool(summary.get("docs_check_performed", summary.get("docs_checked_at_publish"))),
        docs_required=bool(summary.get("docs_required")),
        docs_updated=bool(summary.get("docs_updated")),
        blocked=bool(
            (summary.get("publish_result") == "blocked" or summary.get("final_workflow_result") == "blocked")
            and bool(summary.get("docs_required"))
            and not bool(summary.get("docs_updated"))
        ),
        reason=str(summary.get("docs_reason") or summary.get("publish_detail_reason") or ""),
    )
    print("\n=== VALIDATION RESULT ===")
    print(f"validation_result: {summary.get('validation_result', 'failed')}")
    print(f"validation_state: {summary.get('validation_state', summary.get('validation_result', 'failed'))}")
    print(f"validation_commit_match: {format_bool(summary.get('validation_commit_match'))}")
    print(f"fingerprint_match: {format_bool(summary.get('fingerprint_match'))}")
    print(f"auto_revalidated: {format_bool(summary.get('auto_revalidated'))}")
    print(f"validation_reused: {format_bool(summary.get('validation_reused'))}")
    print(f"auto_revalidation_result: {summary.get('auto_revalidation_result') or 'not_needed'}")
    print(f"validation_stale_detected: {format_bool(summary.get('validation_stale_detected'))}")
    print(f"validation_rerun_attempted: {format_bool(summary.get('validation_rerun_attempted'))}")
    print(f"validation_rerun_result: {summary.get('validation_rerun_result') or 'not_needed'}")
    print(f"validation_commit_updated: {format_bool(summary.get('validation_commit_updated'))}")
    print(f"last_validated_commit: {summary.get('last_validated_commit') or '(none)'}")
    print(f"current_commit: {summary.get('current_commit') or '(none)'}")
    print(f"validation_age_seconds: {summary.get('validation_age_seconds', -1)}")
    print("\n=== POST-SUCCESS PUBLISH ===")
    print(f"publish_requested: {format_bool(summary.get('publish_requested'))}")
    print(f"publish_triggered: {format_bool(summary.get('publish_triggered'))}")
    print(f"publish_mode: {summary.get('publish_mode') or 'validated-run'}")
    print(f"docs_checked_at_publish: {format_bool(summary.get('docs_checked_at_publish'))}")
    print(f"docs_check_performed: {format_bool(docs_reporting.get('docs_check_performed'))}")
    print(f"docs_status: {docs_reporting.get('docs_status') or 'up_to_date'}")
    print(f"docs_reason: {docs_reporting.get('docs_reason') or 'documentation check not performed'}")
    print(f"docs_required: {format_bool(summary.get('docs_required'))}")
    print(f"docs_updated: {format_bool(summary.get('docs_updated'))}")
    print(f"docs_refresh_mode: {summary.get('docs_refresh_mode') or 'none'}")
    print(f"docs_targets: {summary.get('docs_targets') or []}")
    print(f"meaningful_changes_detected: {format_bool(summary.get('meaningful_changes_detected'))}")
    print(f"last_published_commit: {summary.get('last_published_commit') or '(none)'}")
    print(f"current_publish_candidate_commit: {summary.get('current_publish_candidate_commit') or '(none)'}")
    print(f"diff_files_detected: {summary.get('diff_files_detected') or []}")
    print(f"ignored_changes: {summary.get('ignored_changes') or []}")
    print(f"meaningful_paths: {summary.get('meaningful_paths') or []}")
    print(f"auto_stage_attempted: {format_bool(summary.get('auto_stage_attempted'))}")
    print(f"auto_stage_result: {summary.get('auto_stage_result') or 'not_needed'}")
    print(f"auto_staged_paths: {summary.get('auto_staged_paths') or []}")
    print(f"safe_staged_paths: {summary.get('safe_staged_paths') or []}")
    print(f"ignored_nonblocking_paths: {summary.get('ignored_nonblocking_paths') or []}")
    print(f"safe_stage_candidate_paths: {summary.get('safe_stage_candidate_paths') or []}")
    print(f"true_blockers: {summary.get('true_blockers') or []}")
    print(f"blocker_count: {summary.get('blocker_count', 0)}")
    print(f"publishable_ready: {format_bool(summary.get('publishable_ready'))}")
    print(f"blocker_remediation_attempted: {format_bool(summary.get('blocker_remediation_attempted'))}")
    print(f"blocker_remediation_result: {summary.get('blocker_remediation_result') or 'not_needed'}")
    print(f"auto_removed_paths: {summary.get('auto_removed_paths') or []}")
    print(f"auto_ignored_patterns: {summary.get('auto_ignored_patterns') or []}")
    print(f"remaining_true_blockers: {summary.get('remaining_true_blockers') or []}")
    print(f"remaining_unstaged_paths: {summary.get('remaining_unstaged_paths') or []}")
    print(f"remaining_unstaged: {summary.get('remaining_unstaged') or []}")
    print(f"working_tree_clean: {format_bool(summary.get('working_tree_clean', True))}")
    print(f"working_tree_publishable_paths: {summary.get('working_tree_publishable_paths') or []}")
    print(f"working_tree_internal_paths: {summary.get('working_tree_internal_paths') or []}")
    print(f"working_tree_artifact_paths: {summary.get('working_tree_artifact_paths') or []}")
    print(f"staging_summary: {summary.get('staging_summary') or {'auto_staged': 0, 'ignored': 0, 'blocked': 0}}")
    print(f"staging_decision_reason: {summary.get('staging_decision_reason') or '(none)'}")
    print(f"staging_reason: {summary.get('staging_reason') or '(none)'}")
    print(f"blocked_file_analysis: {summary.get('blocked_file_analysis') or []}")
    print(f"blocked_analysis_summary: {summary.get('blocked_analysis_summary') or {}}")
    print(f"base_branch: {summary.get('base_branch') or '(none)'}")
    print(f"prepublish_base_alignment_attempted: {format_bool(summary.get('prepublish_base_alignment_attempted'))}")
    print(f"branch_diverged: {format_bool(summary.get('branch_diverged'))}")
    print(f"alignment_needed: {format_bool(summary.get('alignment_needed'))}")
    print(f"alignment_result: {summary.get('alignment_result') or 'not_needed'}")
    print(f"alignment_changed_commit: {format_bool(summary.get('alignment_changed_commit'))}")
    print(f"validation_rerun_after_alignment: {format_bool(summary.get('validation_rerun_after_alignment'))}")
    if summary.get("alignment_block_reason"):
        print(f"alignment_block_reason: {summary.get('alignment_block_reason')}")
    if summary.get("publish_reason"):
        print(f"publish_reason: {summary['publish_reason']}")
    if summary.get("publish_detail_reason"):
        print(f"publish_detail_reason: {summary['publish_detail_reason']}")
    if not summary.get("publish_result_detail"):
        print("\n=== PUBLISH RESULT ===")
        print(f"publish_result: {summary.get('publish_result', 'not_requested')}")
    print(f"pr_created_or_reused: {format_bool(summary.get('pr_created_or_reused'))}")
    print(f"pr_url: {summary.get('pr_url') or 'none'}")
    print(f"pr_summary: {summary.get('pr_summary') or 'No PR created (expected for this run)'}")
    print(f"pr_merged: {format_bool(summary.get('pr_merged'))}")
    print(f"local_main_synced: {format_bool(summary.get('local_main_synced'))}")
    print(f"pr_mergeable: {summary.get('pr_mergeable') or 'unknown'}")
    print(f"pr_conflicts_detected: {format_bool(summary.get('pr_conflicts_detected'))}")
    print(f"pr_mergeability_source: {summary.get('pr_mergeability_source') or 'github'}")
    print(f"pr_mergeable_final: {summary.get('pr_mergeable_final') or summary.get('pr_mergeable') or 'unknown'}")
    print(f"pr_conflicts_detected_final: {format_bool(summary.get('pr_conflicts_detected_final'))}")
    print(f"pr_mergeability_repair_attempted: {format_bool(summary.get('pr_mergeability_repair_attempted'))}")
    print(f"pr_mergeability_repair_result: {summary.get('pr_mergeability_repair_result') or 'not_needed'}")
    print(f"final_workflow_result: {summary.get('final_workflow_result') or summary.get('publish_result') or 'failed'}")
    if summary.get("pr_mergeability_reason"):
        print(f"pr_mergeability_reason: {summary.get('pr_mergeability_reason')}")
    if summary.get("explain_staging"):
        print("\n=== STAGING FILE DECISIONS ===")
        for item in summary.get("file_decisions") or []:
            print(
                "file_decision: "
                f"path={item.get('path') or '(none)'} "
                f"file_type={item.get('file_type') or 'unknown'} "
                f"classification_source={item.get('classification_source') or 'fallback'} "
                f"publishable={format_bool(item.get('publishable'))} "
                f"action={item.get('action') or 'ignored'} "
                f"reason={item.get('reason') or item.get('publish_reason') or '(none)'}"
            )
    if summary.get("blocked_file_analysis"):
        print("")
        print_publish_block_analysis(
            summary.get("blocked_file_analysis") or [],
            summary.get("blocked_analysis_summary") or {},
        )


def print_publish_summary(publish_result: dict) -> None:
    preflight = publish_result.get("preflight") or {}
    final = publish_result.get("final") or {}
    target = publish_result.get("target") or {}
    environment = publish_result.get("environment") or {}
    fingerprint = publish_result.get("fingerprint") or {}
    actions = publish_result.get("actions") or []
    docs_reporting = summarize_docs_publish_reporting(
        docs_check_performed=bool(
            publish_result.get("docs_check_performed", publish_result.get("docs_checked_at_publish"))
        ),
        docs_required=bool(publish_result.get("docs_required")),
        docs_updated=bool(publish_result.get("docs_updated")),
        blocked=bool(
            publish_result.get("control_path") == "blocked_docs"
            or (
                bool(publish_result.get("docs_required"))
                and not bool(publish_result.get("docs_updated"))
                and bool(publish_result.get("reason"))
            )
        ),
        reason=str(publish_result.get("docs_reason") or publish_result.get("reason") or ""),
    )
    print("\n=== PUBLISH RESULT ===")
    print(f"publish_result: {final.get('status') or 'failed'}")
    print(
        f"resolved_target: {target.get('type') or 'blocked'} "
        f"{target.get('repo') or '(none)'}"
    )
    print(f"control_path: {publish_result.get('control_path') or '(none)'}")
    print(f"state_loaded: {bool(publish_result.get('state_loaded'))}")
    print(f"state_reset: {bool(publish_result.get('state_reset'))}")
    print(f"reused_fork: {bool(publish_result.get('reused_fork'))}")
    print(f"transport_locked: {bool(publish_result.get('transport_locked'))}")
    print(f"state_confidence: {publish_result.get('state_confidence') or 'low'}")
    print(
        "environment: "
        f"ci={bool(environment.get('ci'))} "
        f"github_actions={bool(environment.get('github_actions'))} "
        f"interactive={bool(environment.get('interactive'))}"
    )
    print(f"remote_url: {publish_result.get('remote_url') or '(none)'}")
    print(f"normalized_origin: {publish_result.get('normalized_origin') or '(none)'}")
    print(f"transport: {publish_result.get('auth_transport') or 'unknown'}")
    print(f"branch: {publish_result.get('branch') or '(none)'}")
    print(
        "preflight_auth: "
        f"gh_available={bool(preflight.get('gh_available'))} "
        f"gh_auth={bool(preflight.get('gh_auth'))} "
        f"ssh_auth={bool(preflight.get('ssh_auth'))}"
    )
    print(f"requires_fork: {bool(target.get('requires_fork'))}")
    print(f"fork_created: {bool('fork created with gh' in actions)}")
    print(f"actions: {', '.join(actions) if actions else '(none)'}")
    fixes = publish_result.get("attempted_fixes") or []
    print(f"attempted_fixes: {', '.join(fixes) if fixes else '(none)'}")
    print(f"retry_performed: {bool(publish_result.get('retry_performed'))}")
    print(f"retry_success: {bool(publish_result.get('retry_success'))}")
    print(f"retry_reason: {publish_result.get('retry_reason') or '(none)'}")
    print(f"meaningful_changes_detected: {format_bool(publish_result.get('meaningful_changes_detected'))}")
    print(f"last_published_commit: {publish_result.get('last_published_commit') or '(none)'}")
    print(f"current_publish_candidate_commit: {publish_result.get('current_publish_candidate_commit') or '(none)'}")
    print(f"diff_files_detected: {publish_result.get('diff_files_detected') or []}")
    print(f"ignored_changes: {publish_result.get('ignored_changes') or []}")
    print(f"meaningful_paths: {publish_result.get('meaningful_paths') or []}")
    print(f"auto_stage_attempted: {format_bool(publish_result.get('auto_stage_attempted'))}")
    print(f"auto_stage_result: {publish_result.get('auto_stage_result') or 'not_needed'}")
    print(f"auto_staged_paths: {publish_result.get('auto_staged_paths') or []}")
    print(f"safe_staged_paths: {publish_result.get('safe_staged_paths') or []}")
    print(f"ignored_nonblocking_paths: {publish_result.get('ignored_nonblocking_paths') or []}")
    print(f"safe_stage_candidate_paths: {publish_result.get('safe_stage_candidate_paths') or []}")
    print(f"true_blockers: {publish_result.get('true_blockers') or []}")
    print(f"blocker_count: {publish_result.get('blocker_count', 0)}")
    print(f"publishable_ready: {format_bool(publish_result.get('publishable_ready'))}")
    print(f"blocker_remediation_attempted: {format_bool(publish_result.get('blocker_remediation_attempted'))}")
    print(f"blocker_remediation_result: {publish_result.get('blocker_remediation_result') or 'not_needed'}")
    print(f"auto_removed_paths: {publish_result.get('auto_removed_paths') or []}")
    print(f"auto_ignored_patterns: {publish_result.get('auto_ignored_patterns') or []}")
    print(f"remaining_true_blockers: {publish_result.get('remaining_true_blockers') or []}")
    print(f"remaining_unstaged_paths: {publish_result.get('remaining_unstaged_paths') or []}")
    print(f"remaining_unstaged: {publish_result.get('remaining_unstaged') or []}")
    print(f"staging_summary: {publish_result.get('staging_summary') or {'auto_staged': 0, 'ignored': 0, 'blocked': 0}}")
    print(f"staging_decision_reason: {publish_result.get('staging_decision_reason') or '(none)'}")
    print(f"staging_reason: {publish_result.get('staging_reason') or '(none)'}")
    print(f"blocked_file_analysis: {publish_result.get('blocked_file_analysis') or []}")
    print(f"blocked_analysis_summary: {publish_result.get('blocked_analysis_summary') or {}}")
    print(f"docs_checked_at_publish: {format_bool(publish_result.get('docs_checked_at_publish'))}")
    print(f"docs_check_performed: {format_bool(docs_reporting.get('docs_check_performed'))}")
    print(f"docs_status: {docs_reporting.get('docs_status') or 'up_to_date'}")
    print(f"docs_reason: {docs_reporting.get('docs_reason') or 'documentation check not performed'}")
    print(f"docs_required: {format_bool(publish_result.get('docs_required'))}")
    print(f"docs_updated: {format_bool(publish_result.get('docs_updated'))}")
    print(f"docs_refresh_mode: {publish_result.get('docs_refresh_mode') or 'none'}")
    print(f"docs_targets: {publish_result.get('docs_targets') or []}")
    print(f"base_branch: {publish_result.get('base_branch') or '(none)'}")
    print(f"prepublish_base_alignment_attempted: {format_bool(publish_result.get('prepublish_base_alignment_attempted'))}")
    print(f"branch_diverged: {format_bool(publish_result.get('branch_diverged'))}")
    print(f"alignment_needed: {format_bool(publish_result.get('alignment_needed'))}")
    print(f"alignment_result: {publish_result.get('alignment_result') or 'not_needed'}")
    print(f"alignment_changed_commit: {format_bool(publish_result.get('alignment_changed_commit'))}")
    print(f"validation_rerun_after_alignment: {format_bool(publish_result.get('validation_rerun_after_alignment'))}")
    if publish_result.get("alignment_block_reason"):
        print(f"alignment_block_reason: {publish_result.get('alignment_block_reason')}")
    print(f"validation_state: {publish_result.get('validation_state') or 'success'}")
    print(f"validation_commit_match: {format_bool(publish_result.get('validation_commit_match'))}")
    print(f"fingerprint_match: {format_bool(publish_result.get('fingerprint_match'))}")
    print(f"auto_revalidated: {format_bool(publish_result.get('auto_revalidated'))}")
    print(f"validation_reused: {format_bool(publish_result.get('validation_reused'))}")
    print(f"auto_revalidation_result: {publish_result.get('auto_revalidation_result') or 'not_needed'}")
    print(f"validation_stale_detected: {format_bool(publish_result.get('validation_stale_detected'))}")
    print(f"validation_rerun_attempted: {format_bool(publish_result.get('validation_rerun_attempted'))}")
    print(f"validation_rerun_result: {publish_result.get('validation_rerun_result') or 'not_needed'}")
    print(f"validation_commit_updated: {format_bool(publish_result.get('validation_commit_updated'))}")
    print(f"last_validated_commit: {publish_result.get('last_validated_commit') or '(none)'}")
    print(f"current_commit: {publish_result.get('current_commit') or '(none)'}")
    print(f"validation_age_seconds: {publish_result.get('validation_age_seconds', -1)}")
    print(f"publish_reason: {publish_result.get('publish_reason') or '(none)'}")
    print(f"publish_detail_reason: {publish_result.get('reason') or '(none)'}")
    if publish_result.get("publish_scope") == "current_repo_state":
        working_tree = publish_result.get("working_tree") or {}
        print(
            "working_tree: "
            f"clean={bool(working_tree.get('clean'))} "
            f"unstaged={bool(working_tree.get('has_unstaged'))} "
            f"staged={bool(working_tree.get('has_staged'))} "
            f"untracked={bool(working_tree.get('has_untracked'))}"
        )
        print(f"staged_paths: {working_tree.get('staged_paths') or []}")
        print(f"unstaged_paths: {working_tree.get('unstaged_paths') or []}")
        print(f"untracked_paths: {working_tree.get('untracked_paths') or []}")
    print(f"working_tree_clean: {format_bool(publish_result.get('working_tree_clean', True))}")
    print(f"working_tree_publishable_paths: {publish_result.get('working_tree_publishable_paths') or []}")
    print(f"working_tree_internal_paths: {publish_result.get('working_tree_internal_paths') or []}")
    print(f"working_tree_artifact_paths: {publish_result.get('working_tree_artifact_paths') or []}")
    print(
        "fingerprint: "
        f"matched_previous_success={bool(fingerprint.get('matched_previous_success'))} "
        f"reason={fingerprint.get('reason') or '(none)'}"
    )
    print(f"pr_already_exists: {bool(publish_result.get('pr_already_exists'))}")
    print(f"pr_created_or_reused: {bool(publish_result.get('pr_created_or_reused') or publish_result.get('pr_already_exists'))}")
    print(f"pr_merged: {bool(publish_result.get('pr_merged'))}")
    print(f"local_main_synced: {bool(publish_result.get('local_main_synced'))}")
    print(f"noop: {bool(publish_result.get('noop'))}")
    print(f"final_status: {final.get('status') or 'failed'}")
    print(f"commit_sha: {publish_result.get('commit_sha') or '(none)'}")
    print(f"pr_url: {publish_result.get('pr_url') or 'none'}")
    print(f"pr_summary: {publish_result.get('pr_summary') or 'No PR created (expected for this run)'}")
    print(f"previous_publish_branch: {publish_result.get('previous_publish_branch') or '(none)'}")
    print(f"previous_pr_url: {publish_result.get('previous_pr_url') or '(none)'}")
    print(f"previous_commit: {publish_result.get('previous_commit') or '(none)'}")
    verification = publish_result.get("verification") or {}
    print(f"current_branch: {verification.get('current_branch') or '(none)'}")
    print(f"upstream_branch: {verification.get('upstream_branch') or '(none)'}")
    print(f"upstream_exists: {bool(verification.get('upstream_exists'))}")
    print(f"local_head: {verification.get('local_head') or '(none)'}")
    print(f"remote_head: {verification.get('remote_head') or '(none)'}")
    print(f"sync_verified: {bool(verification.get('synced'))}")
    print(f"pr_requested: {bool(publish_result.get('pr_requested'))}")
    print(f"pr_status: {publish_result.get('pr_status') or 'not_requested'}")
    print(f"pr_mergeable: {publish_result.get('pr_mergeable') or 'unknown'}")
    print(f"pr_conflicts_detected: {format_bool(publish_result.get('pr_conflicts_detected'))}")
    print(f"pr_mergeability_source: {publish_result.get('pr_mergeability_source') or 'github'}")
    print(f"pr_mergeable_final: {publish_result.get('pr_mergeable_final') or publish_result.get('pr_mergeable') or 'unknown'}")
    print(f"pr_conflicts_detected_final: {format_bool(publish_result.get('pr_conflicts_detected_final'))}")
    print(f"pr_mergeability_repair_attempted: {format_bool(publish_result.get('pr_mergeability_repair_attempted'))}")
    print(f"pr_mergeability_repair_result: {publish_result.get('pr_mergeability_repair_result') or 'not_needed'}")
    print(f"final_workflow_result: {publish_result.get('final_workflow_result') or final.get('status') or 'failed'}")
    if publish_result.get("explain_staging"):
        print("\n=== STAGING FILE DECISIONS ===")
        for item in publish_result.get("file_decisions") or []:
            print(
                "file_decision: "
                f"path={item.get('path') or '(none)'} "
                f"file_type={item.get('file_type') or 'unknown'} "
                f"classification_source={item.get('classification_source') or 'fallback'} "
                f"publishable={format_bool(item.get('publishable'))} "
                f"action={item.get('action') or 'ignored'} "
                f"reason={item.get('reason') or item.get('publish_reason') or '(none)'}"
            )
    if publish_result.get("blocked_file_analysis"):
        print("")
        print_publish_block_analysis(
            publish_result.get("blocked_file_analysis") or [],
            publish_result.get("blocked_analysis_summary") or {},
        )
    if publish_result.get("pr_mergeability_reason"):
        print(f"pr_mergeability_reason: {publish_result['pr_mergeability_reason']}")
    if publish_result.get("pr_reason"):
        print(f"pr_reason: {publish_result['pr_reason']}")
    if publish_result.get("publish_scope") == "current_repo_state":
        summary_status = publish_result.get("summary_status") or publish_result.get("reason") or "(none)"
        print(f"mode_summary: {summary_status}")
        if publish_result.get("control_path") == "noop" and publish_result.get("reason") == "no changes to publish":
            print("no changes detected → noop")
    if publish_result.get("reason"):
        print(f"reason: {publish_result['reason']}")
    print(f"next_action: {publish_result.get('next_action') or '(none)'}")
    if final.get("status") in {"success", "noop"} and publish_result.get("recommended_command"):
        print("Next publish command:")
        print(publish_result["recommended_command"])


def maybe_show_diff(repo: Path, show_diff: bool) -> None:
    if not show_diff:
        return
    diff_text = filtered_git_diff_output(repo)
    print("\n=== RESULTING DIFF ===")
    print(diff_text or "(no diff)")


def maybe_prompt_post_success(repo: Path, test_cmd: str, mode: str, show_diff: bool, dry_run: bool) -> str:
    if dry_run or not sys.stdin.isatty():
        maybe_show_diff(repo, show_diff)
        return "commit"

    while True:
        print("\n[1] apply + commit")
        print("[2] show diff")
        print("[3] run full suite")
        print("[4] exit")
        choice = input("Choose an action: ").strip()
        if choice == "1":
            return "commit"
        if choice == "2":
            maybe_show_diff(repo, True)
            continue
        if choice == "3":
            code, output = run_subprocess("pytest -q", repo, shell=True)
            print(output)
            if code == 0:
                print("Full suite passed.")
            else:
                print("Full suite failed.")
            continue
        if choice == "4":
            return "exit"


def compute_success_confidence(
    repo: Path,
    candidate_results: list[dict],
    attempt_number: int,
) -> tuple[str, list[str]]:
    changed_paths = meaningful_changed_paths(repo)
    diff_size = len(filtered_git_diff_output(repo, changed_paths).encode("utf-8")) if changed_paths else 0
    reasons = []
    if changed_paths and len(changed_paths) <= 2 and diff_size <= 2000:
        reasons.append("localized file changes")
    if all(item.get("ok") for item in candidate_results):
        reasons.append("validation accepted without regressions")
    if diff_size > 5000 or len(changed_paths) > 3:
        reasons.append("broader change set")
    if len(candidate_results) <= 1:
        reasons.append("limited validation coverage")
    if CURRENT_VALIDATION_PLAN.get("active") and CURRENT_VALIDATION_PLAN.get("limited_validation"):
        reasons.append("script validation was limited to syntax/import or weak runtime signals")
    if CURRENT_VALIDATION_PLAN.get("active") and CURRENT_VALIDATION_PLAN.get("auto_probe_used"):
        reasons.append("live endpoint probe evidence informed validation planning")

    if changed_paths and len(changed_paths) <= 2 and diff_size <= 2000 and all(item.get("ok") for item in candidate_results):
        level = "HIGH"
    elif diff_size > 5000 or len(changed_paths) > 3:
        level = "LOW"
    else:
        level = "MEDIUM"
    if CURRENT_VALIDATION_PLAN.get("active") and CURRENT_VALIDATION_PLAN.get("limited_validation") and level == "HIGH":
        level = "MEDIUM"
    if CURRENT_VALIDATION_PLAN.get("active") and CURRENT_VALIDATION_PLAN.get("auto_probe_used") and str(CURRENT_VALIDATION_PLAN.get("confidence_level") or "").lower() == "low":
        level = "LOW"

    if not reasons:
        reasons.append(f"validated successfully on attempt {attempt_number}")
    return level, reasons[:3]


def build_failure_signature(failure_type: str, failure_context: dict, precision_patch: dict | None = None) -> dict:
    symbols = []
    if precision_patch and precision_patch.get("symbol"):
        symbols.append(precision_patch["symbol"])
    for candidate in [
        failure_context.get("failing_test_name", "").split("::")[-1],
        failure_context.get("expected_value", ""),
        failure_context.get("actual_value", ""),
    ]:
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", candidate):
            if token not in symbols:
                symbols.append(token)
    return {
        "failure_type": failure_type,
        "test_name": failure_context.get("failing_test_name", ""),
        "symbols": symbols[:5],
    }


def match_pattern_memory(memory: dict, failure_signature: dict) -> dict:
    best_match = None
    best_score = 0
    signature_symbols = set(symbol.lower() for symbol in failure_signature.get("symbols", []))
    signature_test = failure_signature.get("test_name", "").lower()
    for pattern in memory.get("patterns", []):
        if not isinstance(pattern, dict):
            continue
        score = 0
        if pattern.get("failure_type") == failure_signature.get("failure_type"):
            score += 2
        stored_symbols = set(symbol.lower() for symbol in pattern.get("symbols", []))
        overlap = sorted(signature_symbols & stored_symbols)
        score += min(2, len(overlap))
        stored_test = str(pattern.get("test_name", "")).lower()
        if signature_test and stored_test and (
            signature_test == stored_test
            or signature_test.split("::")[-1] == stored_test.split("::")[-1]
        ):
            score += 2
        if score > best_score:
            best_score = score
            best_match = pattern
    if best_match and best_score >= 3:
        return {
            "matched": True,
            "score": best_score,
            "successful_strategy": best_match.get("successful_strategy", ""),
            "successful_files": best_match.get("successful_files", []),
            "successful_symbols": best_match.get("successful_symbols", []),
            "failed_strategies": best_match.get("failed_strategies", []),
        }
    return {
        "matched": False,
        "score": 0,
        "successful_strategy": "",
        "successful_files": [],
        "successful_symbols": [],
        "failed_strategies": [],
    }


def update_pattern_memory(
    memory: dict,
    failure_signature: dict,
    strategy_type: str,
    hypothesis_result: str,
    related_files: list[str],
    related_symbols: list[str],
) -> dict:
    patterns = memory.setdefault("patterns", [])
    entry = None
    for candidate in patterns:
        if (
            candidate.get("failure_type") == failure_signature.get("failure_type")
            and candidate.get("test_name", "") == failure_signature.get("test_name", "")
        ):
            entry = candidate
            break
    if entry is None:
        entry = {
            "failure_type": failure_signature.get("failure_type", ""),
            "test_name": failure_signature.get("test_name", ""),
            "symbols": failure_signature.get("symbols", []),
            "successful_strategy": "",
            "successful_files": [],
            "successful_symbols": [],
            "failed_strategies": [],
        }
        patterns.append(entry)

    if hypothesis_result == "confirmed":
        entry["successful_strategy"] = strategy_type
        entry["successful_files"] = related_files[:5]
        entry["successful_symbols"] = related_symbols[:5]
    elif hypothesis_result == "rejected":
        failed = entry.setdefault("failed_strategies", [])
        if strategy_type and strategy_type not in failed:
            failed.append(strategy_type)
    entry["symbols"] = sorted(set(entry.get("symbols", []) + failure_signature.get("symbols", [])))[:8]
    return memory


def format_failure_context(failure_context: dict) -> str:
    lines = ["Structured failure context:"]
    if failure_context.get("failing_test_name"):
        lines.append(f"- failing_test_name: {failure_context['failing_test_name']}")
    if failure_context.get("failing_assertion"):
        lines.append(f"- failing_assertion: {failure_context['failing_assertion']}")
    if failure_context.get("expected_value"):
        lines.append(f"- expected_value: {failure_context['expected_value']}")
    if failure_context.get("actual_value"):
        lines.append(f"- actual_value: {failure_context['actual_value']}")
    for frame in failure_context.get("stack_frames", []):
        lines.append(
            f"- stack_frame: {frame.get('path', '')}:{frame.get('line', '')} in {frame.get('function', '')}"
        )
    return "\n".join(lines)


def evaluate_test_progress(previous_context: dict, current_context: dict, tests_passed: bool) -> str:
    if tests_passed:
        return "test passes"
    previous_test = previous_context.get("failing_test_name", "")
    current_test = current_context.get("failing_test_name", "")
    previous_assertion = previous_context.get("failing_assertion", "")
    current_assertion = current_context.get("failing_assertion", "")
    if previous_test and previous_test == current_test and previous_assertion == current_assertion:
        return "same failure persists"
    if previous_test and current_test and previous_test != current_test:
        return "different failure appears"
    return "failure changed but same test remains"


def score_attempt(
    previous_failure_signature: str,
    current_failure_signature: str,
    previous_failure_count: int | None,
    current_failure_count: int | None,
    previous_failure_type: str,
    current_failure_type: str,
    diff_reasoning: str,
    structural_breakage: bool = False,
    modified_files_count: int = 0,
    diff_text: str = "",
) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if previous_failure_signature and current_failure_signature != previous_failure_signature:
        score += 1
        reasons.append("test output changed")

    if previous_failure_count is not None and current_failure_count is not None:
        delta = previous_failure_count - current_failure_count
        if delta > 0:
            score += 2
            reasons.append(f"failing tests decreased ({previous_failure_count} -> {current_failure_count})")
        elif delta < 0:
            score -= 2
            reasons.append(f"failing tests increased ({previous_failure_count} -> {current_failure_count})")

    previous_rank = FAILURE_RANK.get(previous_failure_type, 0)
    current_rank = FAILURE_RANK.get(current_failure_type, 0)
    if current_rank > previous_rank:
        score += 1
        reasons.append(f"error type improved ({previous_failure_type} -> {current_failure_type})")
    elif current_rank < previous_rank:
        score -= 1
        reasons.append(f"error type regressed ({previous_failure_type} -> {current_failure_type})")

    if current_failure_type == FAILURE_IMPORT_ERROR and previous_failure_type != FAILURE_IMPORT_ERROR:
        score -= 2
        reasons.append("transitioned into import_error")

    if diff_reasoning and "introduced new issues" in diff_reasoning.lower():
        lowered = diff_reasoning.lower()
        if any(token in lowered for token in ["yes", "did introduce", "introduced new issues: yes"]):
            score -= 2
            reasons.append("diff reasoning reports new issues")

    if structural_breakage:
        score -= 3
        reasons.append("structural validation detected syntax/import breakage")

    diff_size = len(diff_text.encode("utf-8")) if diff_text else 0
    if score > 0 and modified_files_count <= 1 and diff_size and diff_size <= 1500:
        score += 1
        reasons.append("small targeted diff supported the improvement")
    elif score <= 0 and (modified_files_count > 2 or diff_size > 4000):
        score -= 1
        reasons.append("large diff did not show clear improvement")

    if not reasons:
        reasons.append("no clear improvement signal")

    return score, reasons


def classify_attempt_strategy_type(
    strategy_mode: str,
    failure_type: str,
    edit_scope: str,
    diff_text: str,
    precision_patch: dict,
) -> str:
    lowered_scope = edit_scope.lower()
    lowered_diff = diff_text.lower()

    if failure_type == FAILURE_IMPORT_ERROR or "import" in lowered_scope or "import " in lowered_diff:
        return "import_fix"
    if any(token in lowered_scope for token in ["guard", "none", "empty", "missing", "default"]) or any(
        token in lowered_diff for token in ["if ", " is none", "return ", "raise "]
    ):
        return "guard_clause"
    if any(token in lowered_scope for token in ["logic", "condition", "branch", "comparison"]) or precision_patch.get("active"):
        return "logic_fix"
    if strategy_mode == STRATEGY_MINIMAL_PATCH:
        return "minimal_patch"
    if strategy_mode == STRATEGY_BROADER_REWRITE or any(
        token in lowered_scope for token in ["refactor", "restructure", "rewrite"]
    ):
        return "refactor"
    return "fallback/default"


def choose_diversified_strategy_guidance(attempt_history: list[dict]) -> dict:
    strategies = ["minimal_patch", "logic_fix", "guard_clause", "import_fix", "refactor"]
    if not attempt_history:
        return {"preferred": "", "avoid": "", "reason": ""}

    last = attempt_history[-1]
    if last.get("hypothesis_result") == "confirmed" and last.get("strategy_type"):
        return {
            "preferred": last["strategy_type"],
            "avoid": "",
            "reason": (
                f"The previous hypothesis was confirmed. Prefer refining the same {last['strategy_type']} approach."
            ),
        }

    if len(attempt_history) < 2:
        return {"preferred": "", "avoid": "", "reason": ""}

    previous = attempt_history[-2]
    if (
        last.get("strategy_type")
        and last.get("strategy_type") == previous.get("strategy_type")
        and (last.get("score", 0) <= 0 and previous.get("score", 0) <= 0)
    ):
        used_counts: dict[str, int] = {}
        for item in attempt_history:
            strategy = item.get("strategy_type", "")
            if strategy:
                used_counts[strategy] = used_counts.get(strategy, 0) + 1

        preferred = ""
        for strategy in strategies:
            if strategy != last["strategy_type"] and used_counts.get(strategy, 0) == 0:
                preferred = strategy
                break
        if not preferred:
            for strategy in strategies:
                if strategy != last["strategy_type"] and used_counts.get(strategy, 0) < 2:
                    preferred = strategy
                    break

        if preferred:
            return {
                "preferred": preferred,
                "avoid": last["strategy_type"],
                "reason": (
                    f"The last two attempts repeated {last['strategy_type']} without score improvement. "
                    f"Switch to {preferred} next."
                ),
            }

    return {"preferred": "", "avoid": "", "reason": ""}


def strategy_type_to_mode(strategy_type: str) -> str:
    if strategy_type in {"minimal_patch", "import_fix"}:
        return STRATEGY_MINIMAL_PATCH
    if strategy_type in {"logic_fix", "guard_clause"}:
        return STRATEGY_TEST_FIRST_DIAGNOSIS
    if strategy_type == "refactor":
        return STRATEGY_BROADER_REWRITE
    return ""


def apply_memory_strategy_hint(strategy_mode: str, memory_hint: dict, diversification: dict) -> str:
    if diversification.get("preferred"):
        return strategy_mode
    preferred_mode = strategy_type_to_mode(memory_hint.get("successful_strategy", ""))
    if preferred_mode:
        return preferred_mode
    return strategy_mode


def build_attempt_hypothesis(
    failure_type: str,
    strategy_mode: str,
    relevant_context: dict,
    precision_patch: dict,
    failure_context: dict,
    diversification: dict,
    current_plan: dict | None = None,
) -> dict:
    primary_file = precision_patch.get("file") or relevant_context.get("primary_file") or ""
    symbol = precision_patch.get("symbol") or ""
    test_name = failure_context.get("failing_test_name", "")
    expectation_line = failure_context.get("expected_value", "")
    actual_line = failure_context.get("actual_value", "") or failure_context.get("failing_assertion", "")

    if symbol:
        broken = f"{symbol} in {primary_file}" if primary_file else symbol
    elif primary_file:
        broken = primary_file
    else:
        broken = "the implicated implementation path"

    why = actual_line or failure_type.replace("_", " ")
    if diversification.get("preferred"):
        fix_style = diversification["preferred"]
    elif strategy_mode == STRATEGY_MINIMAL_PATCH:
        fix_style = "minimal patch"
    elif strategy_mode == STRATEGY_TEST_FIRST_DIAGNOSIS:
        fix_style = "logic fix"
    else:
        fix_style = "broader rewrite"

    addresses = expectation_line or "the failing test expectation"
    violated = f" from {test_name}" if test_name else ""
    text = (
        f"Hypothesis: {broken} is causing {test_name or failure_type} because {why}; "
        f"a {fix_style} should change the violated condition{violated} so it satisfies {addresses}."
    )
    if current_plan and current_plan.get("steps"):
        step_number = current_plan.get("current_step_index", 0) + 1
        step_text = current_plan["steps"][current_plan.get("current_step_index", 0)]
        text += f" This follows plan step {step_number}: {step_text}"
    symbols = [symbol] if symbol else []
    files = [primary_file] if primary_file else []
    return {"text": text, "symbols": symbols, "files": files}


def evaluate_hypothesis_result(score: int, previous_failure_type: str, current_failure_type: str) -> str:
    if score > 0:
        return "confirmed"
    if score < 0 or FAILURE_RANK.get(current_failure_type, 0) < FAILURE_RANK.get(previous_failure_type, 0):
        return "rejected"
    return "neutral"


def build_attempt_plan(
    relevant_context: dict,
    precision_patch: dict,
    failure_context: dict,
    prior_plan: dict | None = None,
    continue_plan: bool = False,
) -> dict:
    if continue_plan and prior_plan and prior_plan.get("steps"):
        next_index = min(
            prior_plan.get("current_step_index", 0) + 1,
            len(prior_plan["steps"]) - 1,
        )
        return {
            "steps": prior_plan["steps"][:],
            "files": prior_plan.get("files", [])[:],
            "symbols": prior_plan.get("symbols", [])[:],
            "current_step_index": next_index,
        }

    primary_file = precision_patch.get("file") or relevant_context.get("primary_file") or ""
    symbol = precision_patch.get("symbol") or ""
    selected = relevant_context.get("selected", [])
    secondary_file = ""
    for item in selected:
        candidate = item.get("path", "")
        if candidate and candidate != primary_file and not is_test_file_path(candidate):
            secondary_file = candidate
            break
    test_file = relevant_context.get("required_test_files", [""])[0] if relevant_context.get("required_test_files") else ""
    if failure_context.get("stack_frames"):
        frame_path = failure_context["stack_frames"][0].get("path", "")
        if frame_path and frame_path != primary_file and not is_test_file_path(frame_path):
            secondary_file = secondary_file or frame_path
    test_name = failure_context.get("failing_test_name", "")
    expected = failure_context.get("expected_value", "")

    steps = []
    if primary_file and symbol:
        steps.append(f"Update {symbol} in {primary_file} for the code path used by {test_name or 'the failing test'}.")
    elif primary_file:
        steps.append(f"Fix the implicated logic in {primary_file} for the failing test path.")
    else:
        steps.append("Fix the implicated implementation logic.")
    if secondary_file:
        steps.append(f"Adjust related usage in {secondary_file} if the primary fix changes behavior.")
    elif test_file:
        steps.append(f"Re-read {test_file} to confirm the implementation change matches the test expectation.")
    if expected or test_name:
        steps.append(
            f"Verify the updated behavior matches {expected or 'the failing expectation'} in {test_name or 'the failing test'}."
        )

    steps = steps[:3] or ["Fix the implicated implementation logic."]
    return {
        "steps": steps,
        "files": [path for path in [primary_file, secondary_file, test_file] if path],
        "symbols": [symbol] if symbol else [],
        "current_step_index": 0,
    }


def escalate_strategy_mode(strategy_mode: str, levels: int = 1) -> str:
    order = [
        STRATEGY_MINIMAL_PATCH,
        STRATEGY_TEST_FIRST_DIAGNOSIS,
        STRATEGY_BROADER_REWRITE,
    ]
    try:
        index = order.index(strategy_mode)
    except ValueError:
        return strategy_mode
    return order[min(len(order) - 1, index + max(0, levels))]


def determine_strategy_mode(
    failed_test_runs: int,
    repeated_failure_count: int,
    failure_type: str,
    score_escalation_pressure: int = 0,
) -> str:
    if repeated_failure_count >= 2:
        base_mode = STRATEGY_BROADER_REWRITE
    elif failure_type in {FAILURE_SYNTAX_ERROR, FAILURE_IMPORT_ERROR}:
        base_mode = STRATEGY_MINIMAL_PATCH
    elif failure_type in {FAILURE_ASSERTION_FAILURE, FAILURE_RUNTIME_ERROR}:
        base_mode = STRATEGY_TEST_FIRST_DIAGNOSIS
    elif failed_test_runs >= 1:
        base_mode = STRATEGY_TEST_FIRST_DIAGNOSIS
    else:
        base_mode = STRATEGY_MINIMAL_PATCH
    return escalate_strategy_mode(base_mode, score_escalation_pressure)


def is_test_file_path(path: str) -> bool:
    rel = Path(path)
    name = rel.name
    return "tests" in rel.parts or name.startswith("test_") or name.endswith("_test.py")


def extract_relevant_file_context(
    repo: Path,
    test_output: str,
    recent_files: list[str],
    search_files: list[str] | None = None,
    memory_hint: dict | None = None,
    limit: int = 5,
) -> dict:
    all_files = repo_files(repo)
    file_set = set(all_files)
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    traceback_files: set[str] = set()
    test_files: set[str] = set()
    module_names: set[str] = set()

    def add(path: str, score: int, reason: str) -> None:
        if path not in file_set:
            return
        scores[path] = scores.get(path, 0) + score
        reasons.setdefault(path, []).append(reason)

    for match in re.findall(r'File "([^"]+\.py)"', test_output):
        candidate = match.strip()
        if candidate.startswith(str(repo)):
            try:
                candidate = str(Path(candidate).resolve().relative_to(repo))
            except ValueError:
                continue
        if candidate in file_set:
            traceback_files.add(candidate)
            add(candidate, 8, "traceback file")

    for match in re.findall(r"((?:tests?/)?test_[\w/.-]+\.py)", test_output):
        candidate = match.strip()
        if candidate in file_set:
            test_files.add(candidate)
            add(candidate, 7, "failing test file")

    for match in re.findall(r"FAILED\s+([\w/.-]+\.py)::", test_output):
        if match in file_set:
            test_files.add(match)
            add(match, 7, "failed test node")

    for match in re.findall(r"(?:ModuleNotFoundError|ImportError):.*?'([^']+)'", test_output):
        module_names.add(match)
    for match in re.findall(r"(?:from|import)\s+([a-zA-Z_][\w.]*)", test_output):
        module_names.add(match)

    for path in recent_files:
        add(path, 4, "recently modified")
    for path in search_files or []:
        add(path, 5, "repository search hit")
    for path in (memory_hint or {}).get("successful_files", []):
        add(path, 5, "pattern memory successful file")

    tokens = set()
    for path in list(traceback_files) + list(test_files) + recent_files:
        stem = Path(path).stem.replace("test_", "")
        for token in re.split(r"[_\W]+", stem):
            if len(token) >= 3:
                tokens.add(token.lower())

    for module in module_names:
        module_path = module.replace(".", "/")
        for candidate in all_files:
            if candidate.endswith(f"{module_path}.py") or candidate.endswith(f"{module.split('.')[-1]}.py"):
                add(candidate, 6, f"module/import match: {module}")

    for symbol in (memory_hint or {}).get("successful_symbols", []):
        for candidate in all_files:
            stem = Path(candidate).stem.lower()
            if symbol.lower() in stem:
                add(candidate, 3, f"pattern memory symbol match: {symbol}")

    for token in tokens:
        for candidate in all_files:
            stem = Path(candidate).stem.lower()
            if token in stem:
                add(candidate, 2, f"filename similarity: {token}")

    ranked = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            0 if is_test_file_path(item[0]) else 1,
            len(item[0]),
            item[0],
        ),
    )
    selected = []
    for path, score in ranked[: max(3, min(limit, 5))]:
        selected.append(
            {
                "path": path,
                "score": score,
                "reasons": reasons.get(path, []),
            }
        )

    selected_paths = {item["path"] for item in selected}
    required_test_files = sorted(path for path in test_files if path in selected_paths)[:1]
    required_traceback_files = sorted(path for path in traceback_files if path in selected_paths)
    required_impl_files = [
        item["path"]
        for item in selected
        if not is_test_file_path(item["path"]) and item["path"] not in required_traceback_files
    ][:1]

    if not required_impl_files:
        for path, _score in ranked:
            if not is_test_file_path(path):
                required_impl_files = [path]
                if path not in selected_paths:
                    selected.append({"path": path, "score": scores[path], "reasons": reasons.get(path, [])})
                break

    primary_file = required_impl_files[0] if required_impl_files else (selected[0]["path"] if selected else "")
    return {
        "selected": selected[:5],
        "required_test_files": required_test_files,
        "required_impl_files": required_impl_files,
        "required_traceback_files": required_traceback_files,
        "primary_file": primary_file,
    }


def format_relevant_file_context(context: dict) -> str:
    lines = ["Selected relevant files:"]
    for item in context.get("selected", []):
        reason_text = ", ".join(item.get("reasons", [])) or "ranked candidate"
        lines.append(f"- {item['path']}: {reason_text}")
    primary_file = context.get("primary_file")
    if primary_file:
        lines.append(f"Most likely primary file to change: {primary_file}")
    return "\n".join(lines)


def summarize_target_files(context: dict, limit: int = 3) -> str:
    files = [item.get("path", "") for item in context.get("selected", []) if item.get("path")]
    return ", ".join(files[:limit])


def suggest_search_terms(test_output: str, relevant_context: dict, memory_hint: dict | None = None) -> list[str]:
    terms = []
    seen = set()

    def add(term: str) -> None:
        cleaned = term.strip()
        if len(cleaned) < 3 or cleaned in seen:
            return
        seen.add(cleaned)
        terms.append(cleaned)

    failed_node = re.search(r"FAILED\s+([\w/.-]+::([\w\[\]-]+))", test_output)
    if failed_node:
        add(failed_node.group(2))

    for match in re.findall(r"(?:ModuleNotFoundError|ImportError):.*?'([^']+)'", test_output):
        add(match)
    for match in re.findall(r"NameError:\s+name '([^']+)'", test_output):
        add(match)
    for match in re.findall(r"AttributeError: .*? has no attribute '([^']+)'", test_output):
        add(match)
    for match in re.findall(r"E\s+assert\s+(.+)", test_output):
        for symbol in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", match):
            add(symbol)
    for match in re.findall(r"(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", test_output):
        add(match)

    primary_file = relevant_context.get("primary_file") or ""
    if primary_file:
        add(Path(primary_file).stem)
    for symbol in (memory_hint or {}).get("successful_symbols", []):
        add(symbol)

    return terms[:3]


def evaluate_search_requirement(
    test_output: str,
    relevant_context: dict,
    read_files_since_failure: set[str],
    failed_test_runs: int,
    attempt_score: int,
    zero_score_streak: int,
    material_regression: bool,
    failure_type_worsened: bool,
    memory_hint: dict | None = None,
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    selected = relevant_context.get("selected", [])
    top_score = selected[0]["score"] if selected else 0
    primary_file = relevant_context.get("primary_file") or ""

    context_tokens: set[str] = set()
    for path in list(read_files_since_failure) + [item.get("path", "") for item in selected]:
        if not path:
            continue
        context_tokens.add(path.lower())
        stem = Path(path).stem.lower()
        context_tokens.add(stem)
        for token in re.split(r"[_\W]+", stem):
            if len(token) >= 3:
                context_tokens.add(token)

    error_symbols: list[str] = []
    for match in re.findall(r"(?:NameError|AttributeError):.*?'([^']+)'", test_output):
        error_symbols.append(match)
    for match in re.findall(r"(?:ModuleNotFoundError|ImportError):.*?'([^']+)'", test_output):
        error_symbols.append(match.split(".")[-1])
    for match in re.findall(r"FAILED\s+[\w/.-]+::([\w\[\]-]+)", test_output):
        error_symbols.append(match)

    unseen_symbols = []
    for symbol in error_symbols:
        lowered = symbol.lower()
        symbol_parts = [part for part in re.split(r"[_\W]+", lowered) if len(part) >= 3]
        if lowered not in context_tokens and not any(part in context_tokens for part in symbol_parts):
            unseen_symbols.append(symbol)

    if unseen_symbols:
        reasons.append(
            "Error output mentions symbols not covered by current context: "
            + ", ".join(unseen_symbols[:3])
        )
    if failed_test_runs == 1:
        reasons.append("First failed test attempt: search before the first edit to gather missing repo context.")
    if not primary_file or top_score < 6:
        reasons.append("No high-confidence relevant implementation file is selected yet.")
    if zero_score_streak >= 2:
        reasons.append("Stagnation detected after two zero-score attempts.")
    if attempt_score <= -2 or material_regression:
        reasons.append("Previous attempt materially regressed.")
    if failure_type_worsened:
        reasons.append("Failure type worsened, so a search is required before the next attempt.")

    terms = suggest_search_terms(test_output, relevant_context, memory_hint)
    for symbol in unseen_symbols:
        cleaned = symbol.strip()
        if cleaned and cleaned not in terms and len(terms) < 3:
            terms.append(cleaned)

    return reasons, terms[:3]


def extract_precision_patch_context(test_output: str, relevant_context: dict, failure_context: dict | None = None) -> dict:
    selected = relevant_context.get("selected", [])
    if not selected:
        return {"active": False, "file": "", "symbol": "", "reason": ""}

    primary_file = relevant_context.get("primary_file") or selected[0]["path"]
    top_score = selected[0].get("score", 0)
    second_score = selected[1].get("score", 0) if len(selected) > 1 else 0
    top_reasons = selected[0].get("reasons", [])

    symbol = ""
    for pattern in [
        r"NameError:\s+name '([^']+)'",
        r"AttributeError: .*? has no attribute '([^']+)'",
        r"(?:ModuleNotFoundError|ImportError):.*?'([^']+)'",
        r"FAILED\s+[\w/.-]+::([\w\[\]-]+)",
    ]:
        match = re.search(pattern, test_output)
        if match:
            symbol = match.group(1).split(".")[-1]
            break
    if not symbol and failure_context and failure_context.get("stack_frames"):
        symbol = failure_context["stack_frames"][0].get("function", "")

    high_confidence_file = top_score >= 8 and top_score >= second_score + 2
    specific_symbol = bool(symbol and len(symbol) >= 3)
    direct_reason = any(reason in top_reasons for reason in ["traceback file", "module/import match", "repository search hit"])

    if primary_file and ((specific_symbol and direct_reason) or (specific_symbol and high_confidence_file) or top_score >= 10):
        reason = (
            f"High-confidence target in {primary_file}"
            + (f" for symbol {symbol}" if symbol else "")
        )
        return {
            "active": True,
            "file": primary_file,
            "symbol": symbol,
            "reason": reason,
        }

    return {"active": False, "file": "", "symbol": "", "reason": ""}


def build_strategy_guidance(
    strategy_mode: str,
    failure_type: str,
    precision_patch: dict | None = None,
    diversification: dict | None = None,
    hypothesis: dict | None = None,
    current_plan: dict | None = None,
    memory_hint: dict | None = None,
) -> str:
    lines = [
        f"Active repair strategy mode: {strategy_mode}.",
        f"Detected failure type: {failure_type}.",
    ]

    if strategy_mode == STRATEGY_MINIMAL_PATCH:
        lines.extend(
            [
                "Use the smallest viable fix.",
                "Strongly prefer replace_in_file for edits to existing files.",
                "Avoid full-file rewrites unless you are creating a new file.",
            ]
        )
    elif strategy_mode == STRATEGY_TEST_FIRST_DIAGNOSIS:
        lines.extend(
            [
                "Diagnose before editing.",
                "Read at least one relevant test file and one target implementation file before any write tool call.",
                "Keep changes targeted after that diagnosis.",
            ]
        )
    else:
        lines.extend(
            [
                "Previous approaches are not working.",
                "You may take a broader rewrite approach when needed.",
                "write_file is allowed when a larger restructure is necessary.",
            ]
        )

    if precision_patch and precision_patch.get("active"):
        lines.extend(
            [
                "Precision patch mode is active.",
                f"Target file: {precision_patch.get('file', '')}.",
                (
                    f"Target symbol/block: {precision_patch.get('symbol')}. "
                    if precision_patch.get("symbol")
                    else "Target a small localized block in the target file. "
                )
                + "Keep edits within a small localized block (about +/-20 lines) when possible.",
                "Do not modify unrelated parts of the file.",
                "Do not introduce new imports unless required.",
                "Prefer minimal diffs over refactors or broad rewrites.",
            ]
        )

    if diversification and diversification.get("reason"):
        lines.extend(
            [
                diversification["reason"],
                f"Preferred strategy type for this attempt: {diversification.get('preferred', '')}.",
                f"Avoid repeating strategy type: {diversification.get('avoid', '')}.",
            ]
        )

    if memory_hint and memory_hint.get("matched"):
        lines.append("Loaded prior pattern from persistent memory.")
        if memory_hint.get("successful_strategy"):
            lines.append(f"Reusing successful strategy: {memory_hint['successful_strategy']}.")
        if memory_hint.get("failed_strategies"):
            lines.append(
                "Avoiding known failed strategy: "
                + ", ".join(memory_hint.get("failed_strategies", [])[:3])
                + "."
            )

    if hypothesis and hypothesis.get("text"):
        lines.extend(
            [
                hypothesis["text"],
                "Act intentionally against this hypothesis instead of reacting blindly.",
            ]
        )

    if current_plan and current_plan.get("steps"):
        step_number = current_plan.get("current_step_index", 0) + 1
        lines.extend(
            [
                "Plan:",
                *[f"{idx}. {step}" for idx, step in enumerate(current_plan["steps"], start=1)],
                f"Current plan step: {step_number}. Focus on this step in the current attempt.",
            ]
        )

    return "\n".join(lines)


def build_system_prompt(
    strategy_mode: str,
    failure_type: str,
    precision_patch: dict | None = None,
    diversification: dict | None = None,
    hypothesis: dict | None = None,
    current_plan: dict | None = None,
    memory_hint: dict | None = None,
    repair_context: dict | None = None,
) -> str:
    return (
        "You are a careful Python coding agent working in a local repository.\n"
        "You MUST use tools for actions.\n"
        "Do not merely describe tool calls in text.\n"
        "If you want to inspect a diff, call git_diff.\n"
        "If you want to run tests, call run_shell.\n"
        "Use tools to inspect files, run tests, inspect diffs, and patch code.\n"
        "Use git_status and git_diff to inspect your changes.\n"
        "After tests pass, inspect git_diff and optionally commit with a concise message.\n"
        "Do not repeat failed ideas.\n"
        "When tests pass, respond with a short summary.\n"
        f"{format_repair_context_note(repair_context)}\n"
        f"{build_strategy_guidance(strategy_mode, failure_type, precision_patch, diversification, hypothesis, current_plan, memory_hint)}"
    )


def build_user_prompt(
    repo: Path,
    branch_name: str,
    test_cmd: str,
    strategy_mode: str,
    failure_type: str,
    precision_patch: dict | None = None,
    diversification: dict | None = None,
    hypothesis: dict | None = None,
    current_plan: dict | None = None,
    memory_hint: dict | None = None,
    repair_context: dict | None = None,
) -> str:
    validation_note = ""
    if CURRENT_VALIDATION_PLAN.get("active"):
        stack_commands = [step.get("command", "") for step in CURRENT_VALIDATION_PLAN.get("chosen_stack", [])]
        validation_note = (
            "Script validation plan:\n"
            f"- primary validation: {CURRENT_VALIDATION_PLAN.get('primary_command', test_cmd)}\n"
            f"- chosen stack: {', '.join(stack_commands)}\n"
            f"- confidence: {CURRENT_VALIDATION_PLAN.get('confidence_level', 'low')}\n"
        )
        if CURRENT_VALIDATION_PLAN.get("auto_probe_used"):
            probe_finding = (CURRENT_VALIDATION_PLAN.get("probe_findings") or [{}])[0]
            validation_note += (
                "Live probe evidence:\n"
                f"- endpoint: {probe_finding.get('endpoint') or '(none)'}\n"
                f"- probe_type: {probe_finding.get('probe_type') or 'auto'}\n"
                f"- probe_summary: {probe_finding.get('summary') or probe_finding.get('error') or '(none)'}\n"
                f"- probe_reasoning: {CURRENT_VALIDATION_PLAN.get('probe_reasoning') or '(none)'}\n"
            )
    repair_context_note = format_repair_context_note(repair_context)
    return (
        f"Repository: {repo}\n"
        f"Current branch: {branch_name or '(unknown or no git branch)'}\n"
        f"Goal: make this pass: {test_cmd}\n"
        f"{validation_note}"
        f"{repair_context_note}"
        f"{build_strategy_guidance(strategy_mode, failure_type, precision_patch, diversification, hypothesis, current_plan, memory_hint)}\n\n"
        "Suggested workflow:\n"
        "1. Run tests.\n"
        "2. Read relevant files.\n"
        "3. Make the smallest correct patch allowed by the current strategy.\n"
        "4. Re-run tests.\n"
        "5. Inspect git diff.\n"
        "6. If tests pass, commit the fix.\n"
    )


def validate_write_request(
    repo: Path,
    tool_name: str,
    tool_args_json: str,
    strategy_mode: str,
    require_diff_before_write: bool,
    diff_seen_since_failure: bool,
    read_test_files: set[str],
    read_target_files: set[str],
    diagnosis_explanation: str,
    diff_reasoning: str,
    require_diff_reasoning: bool,
    edit_plan: str,
    edit_scope: str,
    test_expectation: str,
    test_alignment: str,
    required_test_files: list[str],
    required_impl_files: list[str],
    required_traceback_files: list[str],
    read_files_since_failure: set[str],
    search_required: bool,
    search_count_this_attempt: int,
    search_trigger_reasons: list[str],
    search_terms: list[str],
    precision_patch: dict,
) -> str | None:
    if tool_name not in {"write_file", "replace_in_file", "append_to_file"}:
        return None

    try:
        args = json.loads(tool_args_json) if tool_args_json else {}
    except json.JSONDecodeError:
        args = {}
    path = args.get("path")

    if search_required and search_count_this_attempt == 0:
        return json.dumps(
            {
                "ok": False,
                "error": "Repository search required before editing",
                "strategy_mode": strategy_mode,
                "reasons": search_trigger_reasons,
                "suggested_terms": search_terms,
            },
            indent=2,
        )

    if require_diff_before_write and not diff_seen_since_failure:
        return json.dumps(
            {
                "ok": False,
                "error": "Repeated failures detected. Call git_diff before making more file edits.",
                "strategy_mode": strategy_mode,
            },
            indent=2,
        )

    if require_diff_reasoning and not diff_reasoning:
        return json.dumps(
            {
                "ok": False,
                "error": "Diff reasoning required before editing",
                "strategy_mode": strategy_mode,
            },
            indent=2,
        )

    if not edit_plan:
        return json.dumps(
            {
                "ok": False,
                "error": "Plan required before editing",
                "strategy_mode": strategy_mode,
            },
            indent=2,
        )

    if not edit_scope:
        return json.dumps(
            {
                "ok": False,
                "error": "Edit scope required before editing",
                "strategy_mode": strategy_mode,
            },
            indent=2,
        )

    if precision_patch.get("active"):
        target_file = precision_patch.get("file", "")
        target_symbol = precision_patch.get("symbol", "")
        lowered_scope = edit_scope.lower()
        if isinstance(path, str) and target_file and path != target_file:
            return json.dumps(
                {
                    "ok": False,
                    "error": "Precision patch mode restricts edits to the targeted file",
                    "strategy_mode": strategy_mode,
                    "target_file": target_file,
                    "requested_file": path,
                },
                indent=2,
            )
        if target_file and target_file.lower() not in lowered_scope:
            return json.dumps(
                {
                    "ok": False,
                    "error": "Precision patch scope must reference the targeted file before editing",
                    "strategy_mode": strategy_mode,
                    "target_file": target_file,
                },
                indent=2,
            )
        if target_symbol and target_symbol.lower() not in lowered_scope:
            return json.dumps(
                {
                    "ok": False,
                    "error": "Precision patch scope must reference the targeted symbol before editing",
                    "strategy_mode": strategy_mode,
                    "target_symbol": target_symbol,
                },
                indent=2,
            )

    if test_expectation and not test_alignment:
        return json.dumps(
            {
                "ok": False,
                "error": "Test expectation alignment required before editing",
                "strategy_mode": strategy_mode,
            },
            indent=2,
        )

    missing_traceback_files = sorted(path for path in required_traceback_files if path not in read_files_since_failure)
    read_required_tests = sorted(path for path in required_test_files if path in read_files_since_failure)
    read_required_impl = sorted(path for path in required_impl_files if path in read_files_since_failure)
    if required_test_files and not read_required_tests:
        return json.dumps(
            {
                "ok": False,
                "error": "Relevant file reads required before editing",
                "strategy_mode": strategy_mode,
                "missing_test_files": required_test_files,
            },
            indent=2,
        )
    if required_impl_files and not read_required_impl:
        return json.dumps(
            {
                "ok": False,
                "error": "Relevant file reads required before editing",
                "strategy_mode": strategy_mode,
                "missing_impl_files": required_impl_files,
            },
            indent=2,
        )
    if missing_traceback_files:
        return json.dumps(
            {
                "ok": False,
                "error": "Relevant file reads required before editing",
                "strategy_mode": strategy_mode,
                "missing_traceback_files": missing_traceback_files,
            },
            indent=2,
        )

    if strategy_mode == STRATEGY_TEST_FIRST_DIAGNOSIS:
        if not diagnosis_explanation:
            return json.dumps(
                {
                    "ok": False,
                    "error": "Diagnosis required before editing",
                    "strategy_mode": strategy_mode,
                },
                indent=2,
            )
        if not read_test_files or not read_target_files:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "Strategy test_first_diagnosis requires reading at least one relevant test file "
                        "and one target implementation file before writing."
                    ),
                    "strategy_mode": strategy_mode,
                    "test_files_read": sorted(read_test_files),
                    "target_files_read": sorted(read_target_files),
                },
                indent=2,
            )

    if tool_name == "write_file" and strategy_mode != STRATEGY_BROADER_REWRITE:
        if isinstance(path, str) and path:
            if repo_path_exists(repo, path):
                return json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"Strategy {strategy_mode} forbids full rewrites of existing files. "
                            "Prefer replace_in_file until broader_rewrite mode."
                        ),
                        "strategy_mode": strategy_mode,
                        "path": path,
                    },
                    indent=2,
                )

    return None


def build_recovery_prompt(
    critique: str,
    strategy_mode: str,
    failure_type: str,
    precision_patch: dict,
    diversification: dict,
    hypothesis: dict,
    current_plan: dict,
    memory_hint: dict,
    repeated_failure_count: int,
    last_failed_attempt_files: list[str],
    require_diff_before_write: bool,
    auto_restored_files: list[str],
    search_guidance: str,
) -> str:
    parts = [
        build_strategy_guidance(
            strategy_mode,
            failure_type,
            precision_patch,
            diversification,
            hypothesis,
            current_plan,
            memory_hint,
        ),
        "",
        "Reviewer critique for the latest failure:",
        critique,
        "",
    ]

    if repeated_failure_count >= 2:
        parts.extend(
            [
                (
                    f"The last {repeated_failure_count} failing test outputs were the same or "
                    "substantially similar."
                ),
                "Change strategy before editing again.",
            ]
        )

    if last_failed_attempt_files:
        parts.extend(
            [
                "Files modified during the last failed attempt:",
                "\n".join(f"- {path}" for path in last_failed_attempt_files),
                "If those edits were a dead end, restore one or more of them with git_restore_file before trying a different approach.",
            ]
        )

    if auto_restored_files:
        parts.extend(
            [
                "Automatically restored before this attempt:",
                "\n".join(f"- {path}" for path in auto_restored_files),
            ]
        )

    if require_diff_before_write:
        parts.append("You must inspect git_diff before any further write tool call.")
    else:
        parts.append("Inspect diffs before making larger changes.")

    if search_guidance:
        parts.extend(["", search_guidance])

    return "\n".join(parts)


def tool_list_files(repo: Path) -> str:
    if is_remote_repo(repo):
        code, output = run_subprocess(
            f"find {shlex.quote(str(repo))} -type f",
            repo,
            shell=True,
        )
        if code != 0:
            return json.dumps({"ok": False, "error": output}, indent=2)
        files = []
        repo_prefix = f"{repo}/"
        for line in output.splitlines():
            rel = line[len(repo_prefix):] if line.startswith(repo_prefix) else line
            if not is_ignored_change_path(rel):
                files.append(rel)
        files.sort()
        return json.dumps({"ok": True, "files": files}, indent=2)
    files = []
    for path in repo.rglob("*"):
        rel = path.relative_to(repo)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if path.is_file():
            files.append(str(rel))
    files.sort()
    return json.dumps({"ok": True, "files": files}, indent=2)


def tool_read_file(repo: Path, path: str, max_chars: int) -> str:
    if is_remote_repo(repo):
        remote_path = remote_repo_path(repo, path)
        code, output = run_subprocess(
            f'test -f {shlex.quote(remote_path)} && cat {shlex.quote(remote_path)}',
            repo,
            shell=True,
        )
        if code != 0:
            return json.dumps({"ok": False, "error": f"File not found: {path}"}, indent=2)
        text = output
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True
        return json.dumps(
            {
                "ok": True,
                "path": path,
                "truncated": truncated,
                "content": text,
            },
            indent=2,
        )
    target = safe_repo_path(repo, path)
    if not target.exists():
        return json.dumps({"ok": False, "error": f"File not found: {path}"}, indent=2)
    if not target.is_file():
        return json.dumps({"ok": False, "error": f"Not a file: {path}"}, indent=2)

    text = target.read_text()
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "truncated": truncated,
            "content": text,
        },
        indent=2,
    )


def tool_write_file(repo: Path, path: str, content: str) -> str:
    if is_remote_repo(repo):
        remote_path = remote_repo_path(repo, path)
        remote_dir = str(PurePosixPath(remote_path).parent)
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(content.rstrip() + "\n")
            tmp_path = Path(tmp.name)
        remote_tmp = f"/tmp/local_fix_agent_{int(time.time() * 1000)}.tmp"
        try:
            run_subprocess(f"mkdir -p {shlex.quote(remote_dir)}", repo, shell=True)
            run_subprocess(f'test -f {shlex.quote(remote_path)} && cp {shlex.quote(remote_path)} {shlex.quote(remote_path + ".bak")} || true', repo, shell=True)
            try:
                proc = subprocess.run(
                    ["scp", *ssh_transport_args(), str(tmp_path), f"{CURRENT_REMOTE_TARGET}:{remote_tmp}"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                blocked = {
                    "kind": "timeout issue",
                    "reason": "remote command timed out",
                    "evidence": f"Timed out while copying a file to {CURRENT_REMOTE_TARGET}: {path}",
                    "needs": "a responsive remote host and working SSH file transfer",
                    "action": "Retry when the remote host is responsive or verify SSH/SCP connectivity manually.",
                }
                set_remote_blocked(blocked)
                return json.dumps({"ok": False, "error": blocked["evidence"]}, indent=2)
            if proc.returncode != 0:
                error_output = ((proc.stdout or "") + (proc.stderr or "")).strip()
                blocked = classify_remote_issue(
                    error_output,
                    CURRENT_REMOTE_TARGET,
                    repo,
                    command=f"scp {path}",
                    stage="file_write",
                )
                if blocked:
                    set_remote_blocked(blocked)
                return json.dumps({"ok": False, "error": error_output}, indent=2)
            code, output = run_subprocess(f"mv {shlex.quote(remote_tmp)} {shlex.quote(remote_path)}", repo, shell=True)
            if code != 0:
                return json.dumps({"ok": False, "error": output}, indent=2)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        return json.dumps(
            {
                "ok": True,
                "path": path,
                "bytes_written": len(content.encode("utf-8")),
                "mode": "full_write",
            },
            indent=2,
        )
    target = safe_repo_path(repo, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup_file(target)
    target.write_text(content.rstrip() + "\n")
    return json.dumps(
        {
            "ok": True,
            "path": path,
            "bytes_written": len(content.encode("utf-8")),
            "mode": "full_write",
        },
        indent=2,
    )


def tool_replace_in_file(repo: Path, path: str, old: str, new: str, count: int = 1) -> str:
    if is_remote_repo(repo):
        read_result = json.loads(tool_read_file(repo, path, DEFAULT_MAX_FILE_CHARS * 10))
        if read_result.get("ok") is not True:
            return json.dumps({"ok": False, "error": f"File not found: {path}"}, indent=2)
        text = read_result.get("content", "")
        occurrences = text.count(old)
        if occurrences == 0:
            return json.dumps(
                {"ok": False, "error": "Old snippet not found in file.", "path": path},
                indent=2,
            )
        updated = text.replace(old, new, count)
        write_result = json.loads(tool_write_file(repo, path, updated.rstrip("\n")))
        if write_result.get("ok") is not True:
            return json.dumps(write_result, indent=2)
        return json.dumps(
            {
                "ok": True,
                "path": path,
                "mode": "replace_in_file",
                "occurrences_before": occurrences,
                "replaced_count": min(count, occurrences),
            },
            indent=2,
        )
    target = safe_repo_path(repo, path)
    if not target.exists():
        return json.dumps({"ok": False, "error": f"File not found: {path}"}, indent=2)

    text = target.read_text()
    occurrences = text.count(old)
    if occurrences == 0:
        return json.dumps(
            {
                "ok": False,
                "error": "Old snippet not found in file.",
                "path": path,
            },
            indent=2,
        )

    backup_file(target)
    updated = text.replace(old, new, count)
    target.write_text(updated)

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "mode": "replace_in_file",
            "occurrences_before": occurrences,
            "replaced_count": min(count, occurrences),
        },
        indent=2,
    )


def tool_append_to_file(repo: Path, path: str, content: str) -> str:
    if is_remote_repo(repo):
        read_result = json.loads(tool_read_file(repo, path, DEFAULT_MAX_FILE_CHARS * 10))
        existing = read_result.get("content", "") if read_result.get("ok") is True else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        write_result = json.loads(tool_write_file(repo, path, existing + content))
        if write_result.get("ok") is not True:
            return json.dumps(write_result, indent=2)
        return json.dumps(
            {
                "ok": True,
                "path": path,
                "mode": "append_to_file",
                "bytes_appended": len(content.encode("utf-8")),
            },
            indent=2,
        )
    target = safe_repo_path(repo, path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        backup_file(target)
        existing = target.read_text()
    else:
        existing = ""

    if existing and not existing.endswith("\n"):
        existing += "\n"

    target.write_text(existing + content)

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "mode": "append_to_file",
            "bytes_appended": len(content.encode("utf-8")),
        },
        indent=2,
    )


def tool_search_repo(repo: Path, term: str) -> str:
    query = term.strip()
    if not query:
        return json.dumps({"ok": False, "error": "Search term is required."}, indent=2)

    cmd = [
        "rg",
        "-n",
        "--no-heading",
        "--color",
        "never",
        "--max-count",
        str(MAX_SEARCH_MATCHES),
        query,
        str(repo),
    ]
    code, output = run_subprocess(cmd, repo)
    if code not in {0, 1}:
        return json.dumps({"ok": False, "error": output or f"search failed for: {query}"}, indent=2)

    matches = []
    files = []
    seen_files = set()
    for line in output.splitlines():
        rel_line = line
        repo_prefix = f"{repo}/"
        if rel_line.startswith(repo_prefix):
            rel_line = rel_line[len(repo_prefix):]
        parts = rel_line.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno, text = parts
        if is_ignored_change_path(path):
            continue
        matches.append({"path": path, "line": int(lineno), "text": text[:200]})
        if path not in seen_files:
            seen_files.add(path)
            files.append(path)

    rendered = json.dumps(
        {
            "ok": True,
            "term": query,
            "files": files,
            "matches": matches,
        },
        indent=2,
    )
    if len(rendered) > MAX_SEARCH_OUTPUT_CHARS:
        rendered = rendered[: MAX_SEARCH_OUTPUT_CHARS - 3] + "..."
    return rendered


def is_command_allowed(command: str) -> bool:
    return any(command.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES)


def tool_run_shell(repo: Path, command: str) -> str:
    if not is_command_allowed(command):
        return json.dumps(
            {
                "ok": False,
                "error": f"Command not allowed: {command}",
                "allowed_prefixes": ALLOWED_COMMAND_PREFIXES,
            },
            indent=2,
        )

    code, output = run_subprocess(command, repo, shell=True)
    return json.dumps(
        {
            "ok": code == 0,
            "returncode": code,
            "command": command,
            "output": output,
        },
        indent=2,
    )


def tool_git_status(repo: Path) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code, output = run_subprocess(["git", "status", "--short", "--untracked-files=all"], repo)
    if code == 0:
        output = "\n".join(filter_status_lines(output)).strip()
    return json.dumps({"ok": code == 0, "output": output}, indent=2)


def tool_git_diff(repo: Path, path: str | None = None) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    if path:
        if path.endswith(".bak"):
            return json.dumps({"ok": True, "path": path, "output": ""}, indent=2)
        safe_repo_path(repo, path)
        cmd = ["git", "diff", "--", path]
    else:
        cmd = ["git", "diff"]

    code, output = run_subprocess(cmd, repo)
    if code == 0 and not path:
        output = filter_unified_diff_text(output)
    return json.dumps({"ok": code == 0, "path": path, "output": output}, indent=2)


def tool_git_commit(repo: Path, message: str, tests_passed: bool) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    if not tests_passed:
        return json.dumps(
            {
                "ok": False,
                "error": "Refusing to commit before the configured test command passes.",
            },
            indent=2,
        )

    status_output = filtered_git_status_output(repo)
    meaningful_paths = meaningful_changed_paths(repo)
    diff_output = filtered_git_diff_output(repo, meaningful_paths)

    if not meaningful_paths:
        return json.dumps(
            {
                "ok": True,
                "skipped": True,
                "reason": "No meaningful changes to commit.",
                "inspected_status": status_output,
                "inspected_diff": diff_output,
            },
            indent=2,
        )

    if len(meaningful_paths) > MAX_COMMIT_PATHS:
        return json.dumps(
            {
                "ok": True,
                "skipped": True,
                "reason": (
                    f"Skipping commit: {len(meaningful_paths)} meaningful paths exceed "
                    f"the limit of {MAX_COMMIT_PATHS}."
                ),
                "inspected_status": status_output,
                "inspected_diff": diff_output,
            },
            indent=2,
        )

    diff_size = len(diff_output.encode("utf-8"))
    if diff_size > MAX_COMMIT_DIFF_BYTES:
        return json.dumps(
            {
                "ok": True,
                "skipped": True,
                "reason": (
                    f"Skipping commit: diff size {diff_size} bytes exceeds "
                    f"the limit of {MAX_COMMIT_DIFF_BYTES} bytes."
                ),
                "inspected_status": status_output,
                "inspected_diff": diff_output,
            },
            indent=2,
        )

    code1, out1 = run_subprocess(["git", "add", "-A", "--", *meaningful_paths], repo)
    if code1 != 0:
        return json.dumps({"ok": False, "error": out1}, indent=2)

    code2, out2 = run_subprocess(["git", "commit", "-m", message], repo)
    return json.dumps(
        {
            "ok": code2 == 0,
            "output": out2,
            "inspected_status": status_output,
            "inspected_diff": diff_output,
            "committed_paths": meaningful_paths,
        },
        indent=2,
    )


def tool_git_restore_file(repo: Path, path: str) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    safe_repo_path(repo, path)
    code, output = run_subprocess(["git", "restore", "--", path], repo)
    return json.dumps({"ok": code == 0, "path": path, "output": output}, indent=2)


def tool_git_restore_all(repo: Path) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code, output = run_subprocess(["git", "restore", "."], repo)
    return json.dumps({"ok": code == 0, "output": output}, indent=2)


def tool_git_new_branch(repo: Path, name: str) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code, output = run_subprocess(["git", "checkout", "-b", name], repo)
    return json.dumps({"ok": code == 0, "branch": name, "output": output}, indent=2)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the repository.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_repo",
            "description": "Search the repository for a symbol or text snippet. Use this when current context is insufficient.",
            "parameters": {
                "type": "object",
                "properties": {"term": {"type": "string"}},
                "required": ["term"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write the full replacement contents of a file in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace an exact snippet in a file with a new snippet. Prefer this over full file rewrites when possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["path", "old", "new"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": "Append content to the end of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a safe shell command inside the repository.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status for the repository.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff for the whole repo or a specific file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Commit current changes with a message after tests pass.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_restore_file",
            "description": "Restore one tracked file to its git state.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_restore_all",
            "description": "Restore all tracked files to their git state.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_new_branch",
            "description": "Create and switch to a new branch.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
]


def call_model(messages, tools=None, tool_choice="auto", max_tokens=1400):
    kwargs = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    return client.chat.completions.create(**kwargs)


def handle_tool(repo: Path, max_file_chars: int, tool_name: str, tool_args_json: str, tests_passed: bool = False) -> str:
    try:
        args = json.loads(tool_args_json) if tool_args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": f"Invalid JSON args: {tool_args_json}"}, indent=2)

    try:
        if tool_name == "list_files":
            return tool_list_files(repo)
        if tool_name == "read_file":
            return tool_read_file(repo, args["path"], max_file_chars)
        if tool_name == "search_repo":
            return tool_search_repo(repo, args["term"])
        if tool_name == "write_file":
            return tool_write_file(repo, args["path"], args["content"])
        if tool_name == "replace_in_file":
            return tool_replace_in_file(repo, args["path"], args["old"], args["new"], args.get("count", 1))
        if tool_name == "append_to_file":
            return tool_append_to_file(repo, args["path"], args["content"])
        if tool_name == "run_shell":
            return tool_run_shell(repo, args["command"])
        if tool_name == "git_status":
            return tool_git_status(repo)
        if tool_name == "git_diff":
            return tool_git_diff(repo, args.get("path"))
        if tool_name == "git_commit":
            return tool_git_commit(repo, args["message"], tests_passed)
        if tool_name == "git_restore_file":
            return tool_git_restore_file(repo, args["path"])
        if tool_name == "git_restore_all":
            return tool_git_restore_all(repo)
        if tool_name == "git_new_branch":
            return tool_git_new_branch(repo, args["name"])
        return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, indent=2)


def extract_json_block(text: str):
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for block in fenced:
        try:
            return json.loads(block)
        except Exception:
            pass

    inline = re.findall(r"(\{.*\})", text, re.S)
    for block in inline:
        try:
            return json.loads(block)
        except Exception:
            pass

    return None


def extract_pseudo_tool_call(text: str):
    data = extract_json_block(text)
    if not isinstance(data, dict):
        return None

    function_name = data.get("function") or data.get("name")
    if not isinstance(function_name, str):
        return None

    if "arguments" in data and isinstance(data["arguments"], dict):
        return function_name, json.dumps(data["arguments"])

    args = data.get("args")
    if isinstance(args, list):
        if function_name in {"read_file", "git_diff", "git_restore_file"} and len(args) >= 1:
            return function_name, json.dumps({"path": args[0]})
        if function_name == "git_new_branch" and len(args) >= 1:
            return function_name, json.dumps({"name": args[0]})
        if function_name == "git_commit" and len(args) >= 1:
            return function_name, json.dumps({"message": args[0]})
    if isinstance(args, dict):
        return function_name, json.dumps(args)

    return None


def extract_diagnosis_explanation(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if extract_pseudo_tool_call(stripped):
        return ""
    if len(stripped.split()) < 12:
        return ""

    lowered = stripped.lower()
    required_phrases = [
        "what failed",
        "why it failed",
        "what needs to change",
    ]
    if all(phrase in lowered for phrase in required_phrases):
        return stripped
    return ""


def extract_diff_reasoning(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if extract_pseudo_tool_call(stripped):
        return ""
    if len(stripped.split()) < 12:
        return ""

    lowered = stripped.lower()
    required_phrases = [
        "what changed",
        "whether the change addressed the failure",
        "whether it introduced new issues",
    ]
    if all(phrase in lowered for phrase in required_phrases):
        return stripped
    return ""


def extract_edit_plan(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if extract_pseudo_tool_call(stripped):
        return ""

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    step_lines = []
    for line in lines:
        if re.match(r"^\d+\.\s+", line) or line.startswith("- "):
            step_lines.append(line)

    if len(step_lines) < 2 or len(step_lines) > 5:
        return ""

    lowered = stripped.lower()
    if not any(token in lowered for token in [".py", "test_", "tests/", "::", "def ", "class "]):
        return ""

    if any(len(line.split()) < 3 for line in step_lines):
        return ""

    return "\n".join(step_lines)


def extract_edit_scope(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if extract_pseudo_tool_call(stripped):
        return ""
    if len(stripped.split()) < 10:
        return ""

    lowered = stripped.lower()
    required_phrases = [
        "file:",
        "target:",
        "why:",
    ]
    if not all(phrase in lowered for phrase in required_phrases):
        return ""
    if not any(token in lowered for token in [".py", "function", "class", "method", "block"]):
        return ""
    return stripped


def extract_test_alignment(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if extract_pseudo_tool_call(stripped):
        return ""
    if len(stripped.split()) < 12:
        return ""

    lowered = stripped.lower()
    required_phrases = [
        "what the test expects",
        "what the current behavior is",
        "what needs to change",
        "how the planned fix satisfies it",
    ]
    if all(phrase in lowered for phrase in required_phrases):
        return stripped
    return ""


def get_critique(history_summary: str, latest_test_output: str) -> str:
    critique_messages = [
        {
            "role": "system",
            "content": (
                "You are a strict reviewer of a coding agent. "
                "Given prior attempts and the latest failing test output, "
                "identify what likely went wrong and what the next attempt should do differently. "
                "Prefer small targeted changes and using diff inspection before more edits."
            ),
        },
        {
            "role": "user",
            "content": (
                "Prior attempt summary:\n"
                f"{history_summary}\n\n"
                "Latest failing test output:\n"
                f"{latest_test_output}\n\n"
                "Return a short critique and next-step advice."
            ),
        },
    ]
    resp = call_model(critique_messages, tools=None, max_tokens=300)
    return (resp.choices[0].message.content or "").strip()


def ensure_branch_per_run(repo: Path) -> str:
    if not is_git_repo(repo):
        return ""

    current = current_git_branch(repo)
    branch_name = f"agent-run-{int(time.time())}"

    code, output = run_subprocess(["git", "checkout", "-b", branch_name], repo)
    if code == 0:
        print(f"Created branch: {branch_name}")
        return branch_name

    print(f"Branch creation skipped: {output}")
    if current:
        print(f"Continuing on existing branch: {current}")
        return current

    return ""


def finalize_success(
    repo: Path,
    max_file_chars: int,
    messages,
    tests_passed: bool,
    attempt_number: int,
    test_cmd: str,
    failure_context: dict,
    primary_file: str,
    best_attempt: dict | None,
    current_strategy_type: str,
    dry_run: bool,
    show_diff: bool,
    mode: str,
    publish_requested: bool,
    publish_message: str,
) -> dict:
    progress("final validation...")
    final_resp = call_model(messages, tools=None, max_tokens=300)
    final_text = final_resp.choices[0].message.content or "Tests passed."
    final_text = final_text.strip()

    print("\n=== FINAL RESPONSE ===")
    print(final_text)

    print("\n=== PRE-COMMIT EXPLANATION ===")
    print(final_text)

    status_result = handle_tool(repo, max_file_chars, "git_status", "{}", tests_passed)
    print("\n=== PRE-COMMIT GIT STATUS ===")
    print(status_result)

    diff_result = handle_tool(repo, max_file_chars, "git_diff", "{}", tests_passed)
    print("\n=== PRE-COMMIT GIT DIFF ===")
    print(diff_result)

    structural_ok, structural_output, structural_failure_type = validate_structural_safety(
        repo,
        meaningful_changed_paths(repo),
    )
    print("\n=== PRE-COMMIT STRUCTURAL CHECK ===")
    print(
        json.dumps(
            {
                "ok": structural_ok,
                "failure_type": structural_failure_type,
                "output": structural_output,
            },
            indent=2,
        )
    )
    if not structural_ok:
        print("\n=== AUTO-COMMIT SKIPPED ===")
        print("Skipping commit because structural validation failed.")
        return {"committed": False, "rejected": True, "output": structural_output, "failure_type": structural_failure_type}

    candidates = build_candidate_patches(repo, primary_file, best_attempt, current_strategy_type)
    candidate_selection = select_best_candidate(repo, test_cmd, failure_context, candidates)
    chosen_candidate = candidate_selection.get("chosen", {})
    if not chosen_candidate:
        print("\n=== PRE-COMMIT PATCH VALIDATION ===")
        print("pre-commit validation rejected patch")
        return {"committed": False, "rejected": True, "output": "No candidate patches available.", "failure_type": FAILURE_UNKNOWN, "candidate_results": candidate_selection.get("results", [])}

    if chosen_candidate.get("candidate_name") != "current_patch":
        applied, apply_output = apply_candidate_to_repo(
            repo,
            next(
                (candidate["diff_text"] for candidate in candidates if candidate["name"] == chosen_candidate.get("candidate_name")),
                "",
            ),
        )
        if not applied:
            print("\n=== PRE-COMMIT PATCH VALIDATION ===")
            print("pre-commit validation rejected patch")
            return {
                "committed": False,
                "rejected": True,
                "output": apply_output,
                "failure_type": FAILURE_UNKNOWN,
                "candidate_results": candidate_selection.get("results", []),
            }

    precommit_validation = chosen_candidate
    print("\n=== PRE-COMMIT PATCH VALIDATION ===")
    print(json.dumps(precommit_validation, indent=2))
    if not precommit_validation.get("ok"):
        print("pre-commit validation rejected patch")
        return {
            "committed": False,
            "rejected": True,
            "output": precommit_validation.get("output", ""),
            "failure_type": precommit_validation.get("failure_type", FAILURE_UNKNOWN),
            "status": precommit_validation.get("status", "rejected"),
            "candidate_results": candidate_selection.get("results", []),
            "chosen_candidate": chosen_candidate.get("candidate_name", ""),
        }
    print("pre-commit validation passed")
    confidence_level, confidence_reasons = compute_success_confidence(
        repo,
        candidate_selection.get("results", []),
        attempt_number,
    )
    success_signal(f"Fix found (attempt {attempt_number})")
    print(f"Confidence: {confidence_level}")
    print("Reason:")
    for reason in confidence_reasons:
        print(f"- {reason}")

    action = maybe_prompt_post_success(repo, test_cmd, mode, show_diff, dry_run)
    if action == "exit":
        print("\n=== AUTO-COMMIT SKIPPED ===")
        print("Exited after successful validation without committing.")
        return {
            "committed": False,
            "rejected": False,
            "output": "user exited after validation",
            "failure_type": FAILURE_UNKNOWN,
            "candidate_results": candidate_selection.get("results", []),
            "chosen_candidate": chosen_candidate.get("candidate_name", ""),
        }

    if dry_run:
        print("\n=== AUTO-COMMIT SKIPPED ===")
        print("Dry-run mode enabled. Commit skipped after successful validation.")
        return {
            "committed": False,
            "rejected": False,
            "output": "dry-run: commit skipped",
            "failure_type": FAILURE_UNKNOWN,
            "candidate_results": candidate_selection.get("results", []),
            "chosen_candidate": chosen_candidate.get("candidate_name", ""),
        }

    changed_paths = meaningful_changed_paths(repo)
    if publish_requested:
        print("\n=== AUTO-COMMIT SKIPPED ===")
        print("Publish mode enabled. Deferring commit to the guarded publish workflow.")
        return {
            "committed": False,
            "rejected": False,
            "output": "publish mode: commit deferred",
            "failure_type": FAILURE_UNKNOWN,
            "candidate_results": candidate_selection.get("results", []),
            "chosen_candidate": chosen_candidate.get("candidate_name", ""),
            "changed_paths": changed_paths,
            "confidence_level": confidence_level,
            "confidence_reasons": confidence_reasons,
            "final_text": final_text,
        }

    commit_message = make_commit_message(final_text)
    print("\n=== AUTO-COMMIT MESSAGE ===")
    print(commit_message)

    commit_result = handle_tool(
        repo,
        max_file_chars,
        "git_commit",
        json.dumps({"message": commit_message}),
        tests_passed,
    )
    print("\n=== AUTO-COMMIT RESULT ===")
    print(commit_result)
    try:
        commit_data = json.loads(commit_result)
    except json.JSONDecodeError:
        commit_data = {}
    return {
        "committed": bool(commit_data.get("ok")),
        "rejected": False,
        "output": commit_result,
        "failure_type": FAILURE_UNKNOWN,
        "candidate_results": candidate_selection.get("results", []),
        "chosen_candidate": chosen_candidate.get("candidate_name", ""),
        "changed_paths": commit_data.get("committed_paths", changed_paths),
        "confidence_level": confidence_level,
        "confidence_reasons": confidence_reasons,
        "final_text": final_text,
        "commit_message": commit_message,
        "commit_sha": parse_head_commit(repo) if commit_data.get("ok") else "",
    }


def main():
    global CURRENT_VALIDATION_PLAN
    global CURRENT_NO_AUTO_CONFLICT_RESOLUTION_AFTER_SYNC
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true", help="Launch the top-level interactive terminal app.")
    parser.add_argument("--repo", help="Path to repo")
    parser.add_argument("--script", help="Path to a Python script; validation commands are discovered automatically.")
    parser.add_argument("--config-file", help="Path to a config file for validate/cleanup/compare/generate/align workflows.")
    parser.add_argument("--config-task", choices=sorted(CONFIG_TASKS), help="Config workflow task: validate, cleanup, compare, generate, or align.")
    parser.add_argument("--config-type", choices=sorted(CONFIG_TYPES), default="auto", help="Config workflow type override.")
    parser.add_argument("--config-compare", help="Comparison or style-reference config path for compare/align tasks.")
    parser.add_argument("--config-validation-cmd", help="Custom validation command for config workflows.")
    parser.add_argument("--target", help="SSH host for remote execution.")
    parser.add_argument("--test-cmd", help="Test command")
    parser.add_argument("--repair-context-file", help="JSON file with targeted repair context from publish validation analysis.")
    parser.add_argument("--analyze-validation-failure", action="store_true", help="Analyze the latest publish/finalization validation failure and emit structured targeting context.")
    parser.add_argument("--validation-output-file", help="Optional text file containing validation or publish output to analyze.")
    parser.add_argument("test_cmd_positional", nargs=argparse.REMAINDER, help="Optional test command for wrapper usage.")
    parser.add_argument("--mode", choices=sorted(RUN_MODES.keys()), help="Preset run mode")
    parser.add_argument("--last", action="store_true", help="Reuse the last repo and, if needed, the last test command. Works well with --publish.")
    parser.add_argument("--continue", dest="continue_run", action="store_true", help="Continue using the last repo, test command, and mode.")
    parser.add_argument("--from-last-failure", action="store_true", help="Reuse repo/test/mode from the most recent failed run.")
    parser.add_argument("--reuse-last-test", action="store_true", help="Reuse the most recent test command.")
    parser.add_argument("--dry-run", action="store_true", help="Run the agent but skip commit.")
    parser.add_argument("--explain-only", action="store_true", help="Print resolved settings and exit.")
    parser.add_argument("--show-diff", action="store_true", help="Show the resulting diff automatically after a successful run.")
    parser.add_argument("--publish", action="store_true", help="Compatibility flag for the default validated-run publish behavior.")
    parser.add_argument("--publish-on-success", action="store_true", help="Compatibility flag; publish-on-success is already the default for validated runs.")
    parser.add_argument("--no-publish-on-success", action="store_true", help="Disable the default publish-after-validation-success behavior for validated runs.")
    parser.add_argument("--no-finalize", action="store_true", help="Stop after successful validation and skip the canonical finalization publish path.")
    parser.add_argument("--ensure-validation-record", action="store_true", help="Create or reuse a commit-linked validation record for the current repo state before finalization.")
    parser.add_argument("--script-validate-only", action="store_true", help="For --script workflows, run the discovered validation stack without entering the repair loop.")
    parser.add_argument("--sync-conflicts", action="store_true", help="Inspect and repair merge conflicts for the current repo without entering the repair loop.")
    parser.add_argument("--publish-only", action="store_true", help="Publish the current repo state without running the repair loop or requiring a failing test command.")
    parser.add_argument("--force-publish", action="store_true", help="Allow publish to proceed even when the current validation state is blocked or failed.")
    parser.add_argument("--no-auto-stage", action="store_true", help="Disable automatic staging of safe publishable files during finalization/publish.")
    parser.add_argument("--no-auto-remediate-blockers", action="store_true", help="Disable automatic remediation of high-confidence safe publish blockers during finalization/publish.")
    parser.add_argument("--explain-staging", action="store_true", help="Print per-file staging classification and reasoning in publish/finalization summaries.")
    parser.add_argument("--no-auto-merge-conflicts", action="store_true", help="Detect merge conflicts and block immediately instead of attempting auto-resolution.")
    parser.add_argument("--no-auto-conflict-resolution-after-sync", action="store_true", help="After pull/merge/rebase/branch-sync conflicts, block immediately instead of attempting auto-resolution.")
    parser.add_argument("--no-auto-revalidate", action="store_true", help="Disable automatic validation rerun when publish detects a stale validated commit.")
    parser.add_argument("--force-upstream-merge", action="store_true", help="Allow upstream sync to proceed even when the incoming upstream change analysis is high risk.")
    parser.add_argument("--publish-branch", help="Branch name to use for publish mode.")
    parser.add_argument("--publish-pr", action="store_true", help="After publish, attempt to open a pull request with GitHub CLI.")
    parser.add_argument("--publish-merge", action="store_true", help="After publish, auto-merge only when the PR is a safe self-owned fork PR.")
    parser.add_argument("--publish-merge-local-main", action="store_true", help="After a successful safe auto-merge, sync local main with origin/main.")
    parser.add_argument("--publish-message", help="Commit message to use for publish mode.")
    parser.add_argument("--learn-from", nargs="+", help="Learn reusable script patterns from trusted example scripts.")
    parser.add_argument("--pattern-repo", help="Pattern repo override: auto, none, a configured repo name, or an explicit path.")
    parser.add_argument("--reset-pattern-repo", action="store_true", help="Delete and recreate the private training repo before continuing.")
    parser.add_argument("--import-pattern-files", nargs="+", help="Import external scripts into the private pattern repository and learn from them.")
    parser.add_argument("--import-pattern-repo", help="Import a local repo or folder of scripts into the private pattern repository.")
    parser.add_argument("--add-to-training", action="store_true", help="When used with --script, sanitize and import that script into the training repo before continuing.")
    parser.add_argument("--pattern-trust", choices=sorted(PATTERN_TRUST_LEVELS), default="trusted", help="Trust level for imported or directly learned pattern scripts.")
    parser.add_argument("--pattern-tags", help="Comma-separated tags to attach to imported pattern sources.")
    parser.add_argument("--pattern-note", help="Optional note recorded with imported pattern sources.")
    parser.add_argument("--pattern-include", action="append", help="Glob filter for repo/folder pattern import. Defaults to *.py and may be passed multiple times.")
    parser.add_argument("--pattern-exclude", action="append", help="Exclude glob for repo/folder pattern import. May be passed multiple times.")
    parser.add_argument("--pattern-max-files", type=int, default=DEFAULT_PATTERN_REPO_MAX_FILES, help="Maximum files to scan during repo/folder pattern import.")
    parser.add_argument("--pattern-max-depth", type=int, default=0, help="Maximum relative directory depth to scan during repo/folder pattern import. 0 means unlimited.")
    parser.add_argument("--list-patterns", action="store_true", help="List learned patterns from the private pattern repository.")
    parser.add_argument("--list-pattern-sources", action="store_true", help="List source files tracked in the private pattern repository.")
    parser.add_argument("--promote-pattern", help="Promote one pattern by id.")
    parser.add_argument("--demote-pattern", help="Demote one pattern by id.")
    parser.add_argument("--show-promotion-state", action=argparse.BooleanOptionalAction, default=True, help="Show promotion state in human-readable pattern listings.")
    parser.add_argument("--filter-state", choices=["candidate", "curated_experimental", "curated_trusted"], help="Filter listed patterns or sources by promotion state.")
    parser.add_argument("--filter-tag", help="Filter listed patterns or sources by tag.")
    parser.add_argument("--search", help="Filter listed patterns or sources by substring match.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of patterns or sources to list. 0 means no limit.")
    parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format for list-style pattern commands.")
    parser.add_argument("--promote-source", help="Promote one pattern source by id or path.")
    parser.add_argument("--demote-source", help="Demote one pattern source by id or path.")
    parser.add_argument("--forget-source", help="Forget one pattern source by id or path.")
    parser.add_argument("--set-trust", choices=sorted(PATTERN_TRUST_LEVELS), help="Manual trust override for pattern/source management.")
    parser.add_argument("--set-promotion-state", choices=["candidate", "curated_experimental", "curated_trusted"], help="Manual promotion-state override for pattern/source management.")
    parser.add_argument("--relearn-patterns", action="store_true", help="Rebuild pattern memory by scanning the private pattern repository.")
    parser.add_argument("--forget-pattern", help="Remove a learned pattern by id from the private pattern memory.")
    parser.add_argument("--new-script", help="Generate a new script using learned conventions when relevant patterns exist.")
    parser.add_argument("--new-script-purpose", help="Short purpose statement for --new-script generation.")
    parser.add_argument("--new-script-validation-mode", choices=["auto", "syntax", "cli_help", "custom", "skip"], default="auto", help="Validation mode override for --new-script.")
    parser.add_argument("--compare-pattern-baseline", action="store_true", help="For learned runs, compare the selected pattern-repo plan against a no-pattern baseline.")
    parser.add_argument("--eval-pattern-learning", action="store_true", help="Run baseline vs learned evals for the script-pattern memory.")
    parser.add_argument("--pattern-eval-tasks", help="Optional path to a pattern-learning eval task JSON file.")
    parser.add_argument("--http-proxy", help="HTTP proxy for subprocess-driven tasks.")
    parser.add_argument("--https-proxy", help="HTTPS proxy for subprocess-driven tasks.")
    parser.add_argument("--probe-url", help="Bounded live endpoint probe for API or M3U8 inspection.")
    parser.add_argument("--probe-type", choices=["auto", "head", "get", "json_summary", "headers_summary", "m3u8_summary"], default="auto", help="Probe mode for --probe-url.")
    parser.add_argument("--probe-header", action="append", help="Custom probe header in 'Name: value' form. May be passed multiple times.")
    parser.add_argument("--probe-bearer-token", help="Bearer token to send with --probe-url. Output is redacted.")
    parser.add_argument("--probe-cookie", help="Cookie header to send with --probe-url. Output is redacted.")
    parser.add_argument("--probe-user-agent", help="Override User-Agent for --probe-url.")
    parser.add_argument("--probe-method", help="Override HTTP method for --probe-url.")
    parser.add_argument("--probe-timeout", type=int, default=DEFAULT_PROBE_TIMEOUT_SECONDS, help="Per-request timeout in seconds for --probe-url.")
    parser.add_argument("--probe-max-bytes", type=int, default=DEFAULT_PROBE_MAX_BYTES, help="Maximum response bytes to read for --probe-url.")
    parser.add_argument("--probe-follow-up", type=int, default=DEFAULT_PROBE_FOLLOW_UP_LIMIT, help="Maximum bounded follow-up requests for M3U8 variant or segment probing.")
    parser.add_argument("--no-upstream-sync", action="store_true", help="Skip the automatic upstream fetch/merge check before learning, validation, repair, or publish.")
    parser.add_argument("--api-budget-run", type=int, help="Optional likely API-failure budget for the full run.")
    parser.add_argument("--api-budget-attempt", type=int, help="Optional likely API-failure budget per attempt.")
    parser.add_argument("--config", help="Path to config JSON file.")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-file-chars", type=int)
    args = parser.parse_args()
    raw_argv = sys.argv[1:]
    if not args.config:
        args.config = cli_option_value(raw_argv, "--config")
    if not args.pattern_repo:
        args.pattern_repo = cli_option_value(raw_argv, "--pattern-repo")
    if args.interactive:
        initial_session = {
            "repo": str(Path(args.repo).resolve()) if args.repo else str(Path.cwd()),
            "http_proxy": args.http_proxy or os.environ.get("HTTP_PROXY", ""),
            "https_proxy": args.https_proxy or os.environ.get("HTTPS_PROXY", ""),
            "output": args.output or "human",
        }
        raise SystemExit(run_interactive_app(initial_session))

    if args.analyze_validation_failure:
        repo_value = Path(args.repo).expanduser() if args.repo else Path.cwd()
        if not repo_value.is_absolute():
            repo_value = (Path.cwd() / repo_value).resolve()
        publish_output = ""
        if args.validation_output_file:
            output_path = Path(args.validation_output_file).expanduser()
            if not output_path.is_absolute():
                output_path = (Path.cwd() / output_path).resolve()
            publish_output = output_path.read_text() if output_path.exists() else ""
        analysis = analyze_validation_failure(
            repo_value,
            publish_output=publish_output,
        )
        if args.output == "json":
            print(json.dumps(analysis, indent=2, sort_keys=True))
        else:
            print(format_validation_failure_analysis(analysis))
        return

    pattern_special_mode = bool(
        args.learn_from
        or args.new_script
        or args.eval_pattern_learning
        or args.import_pattern_files
        or args.import_pattern_repo
        or args.reset_pattern_repo
        or args.list_patterns
        or args.list_pattern_sources
        or args.promote_pattern
        or args.demote_pattern
        or args.relearn_patterns
        or args.forget_pattern
        or args.promote_source
        or args.demote_source
        or args.forget_source
        or (args.script and args.add_to_training)
    )

    repo, args.test_cmd, args.max_steps, args.max_file_chars, resolved_mode, mode_source, config_path, recent_state_path, safety_settings, target = resolve_run_settings(
        args,
        require_test_cmd=not args.publish_only and not pattern_special_mode and not args.ensure_validation_record and not args.probe_url and not args.sync_conflicts and not args.script_validate_only and not args.config_file,
    )
    if not target and not repo.exists():
        print(f"Missing repo path: {repo}", file=sys.stderr)
        raise SystemExit(1)

    configure_execution_target(target, repo)
    configure_subprocess_safety(safety_settings)
    CURRENT_NO_AUTO_CONFLICT_RESOLUTION_AFTER_SYNC = bool(getattr(args, "no_auto_conflict_resolution_after_sync", False))
    config_values, _ = load_agent_config(args.config, Path.cwd())
    configure_publish_ignore_paths(config_values)
    if CURRENT_VALIDATION_PLAN.get("active"):
        CURRENT_VALIDATION_PLAN = maybe_enrich_validation_plan_with_probes(CURRENT_VALIDATION_PLAN)
    probe_headers: dict[str, str] = {}
    generation_probe_result: dict = {}
    if args.probe_url:
        try:
            probe_headers = parse_probe_header_args(args.probe_header)
        except ValueError as exc:
            raise SystemExit(str(exc))
        probe_result = probe_endpoint(
            args.probe_url,
            probe_type=args.probe_type,
            method=args.probe_method or "",
            custom_headers=probe_headers,
            bearer_token=args.probe_bearer_token or "",
            cookies=args.probe_cookie or "",
            user_agent=args.probe_user_agent or "",
            http_proxy=args.http_proxy or "",
            https_proxy=args.https_proxy or "",
            timeout_seconds=max(1, int(args.probe_timeout or DEFAULT_PROBE_TIMEOUT_SECONDS)),
            max_bytes=max(512, int(args.probe_max_bytes or DEFAULT_PROBE_MAX_BYTES)),
            follow_up_limit=max(0, int(args.probe_follow_up or DEFAULT_PROBE_FOLLOW_UP_LIMIT)),
        )
        if not args.new_script:
            print_probe_result(probe_result, output_format=args.output)
            if not probe_result.get("ok"):
                raise SystemExit(1)
            return
        generation_probe_result = probe_result
    if args.config_file:
        config_target_path = Path(args.config_file).expanduser()
        if not config_target_path.is_absolute():
            config_target_path = (repo / config_target_path).resolve()
        compare_target = str(args.config_compare or "").strip()
        if compare_target:
            compare_path = Path(compare_target).expanduser()
            if not compare_path.is_absolute():
                compare_target = str((repo / compare_path).resolve())
            else:
                compare_target = str(compare_path)
        result = run_config_workflow(
            repo,
            config_path=config_target_path,
            task=str(args.config_task or "validate"),
            config_type=str(args.config_type or "auto"),
            compare_path=compare_target,
            validation_command=str(args.config_validation_cmd or ""),
            pattern_repo_value=str(args.pattern_repo or ""),
        )
        print_config_workflow_result(result)
        if not result.get("ok"):
            raise SystemExit(1)
        return
    pattern_repo_mutation_mode = bool(
        args.learn_from
        or args.import_pattern_files
        or args.import_pattern_repo
        or args.reset_pattern_repo
        or args.relearn_patterns
        or args.forget_pattern
        or args.promote_pattern
        or args.demote_pattern
        or args.promote_source
        or args.demote_source
        or args.forget_source
        or (args.script and args.add_to_training)
    )
    selection_task_type, selection_task_text, selection_script_path = infer_pattern_repo_selection_context(args, resolved_mode)
    pattern_repo_selection = select_pattern_repo(
        config_values,
        args.pattern_repo,
        selection_task_type,
        selection_task_text,
        script_path=selection_script_path,
        require_repo=pattern_repo_mutation_mode,
    )
    pattern_repo = pattern_repo_selection.get("path")
    created_pattern_repo = False
    reset_existing = False
    json_list_mode = bool((args.list_patterns or args.list_pattern_sources) and args.output == "json")
    if pattern_repo_mutation_mode:
        if pattern_repo is None:
            print("Pattern repo management requires a concrete pattern repo.", file=sys.stderr)
            raise SystemExit(2)
        if args.reset_pattern_repo:
            pattern_repo, reset_existing = reset_pattern_repo(pattern_repo)
        else:
            pattern_repo, created_pattern_repo = ensure_pattern_repo_status(pattern_repo)
    publish_requested = resolve_publish_requested(args)
    if not json_list_mode:
        if args.learn_from:
            startup_signal("Mode: learn trusted script patterns")
        elif (
            args.import_pattern_files
            or args.list_patterns
            or args.list_pattern_sources
            or args.relearn_patterns
            or args.forget_pattern
            or args.promote_pattern
            or args.demote_pattern
            or args.promote_source
            or args.demote_source
            or args.forget_source
        ):
            startup_signal("Mode: manage private pattern repository")
        elif args.new_script:
            startup_signal("Mode: generate new script from learned patterns")
        elif args.eval_pattern_learning:
            startup_signal("Mode: evaluate learned script patterns")
        elif args.publish_only:
            startup_signal("Mode: publish current repo state")
        elif args.ensure_validation_record:
            startup_signal("Mode: ensure validation record")
        elif args.script_validate_only:
            startup_signal("Mode: validate script only")
        elif args.sync_conflicts:
            startup_signal("Mode: sync/repair repo conflicts")
        else:
            startup_signal("Mode: publish validated run" if publish_requested else f"Mode: {resolved_mode}" + (f" ({mode_source})" if mode_source != "explicit" else ""))
            startup_signal(f"Using test: {args.test_cmd}")
        startup_signal(
            "Proxy support: enabled"
            if API_SAFETY_STATE["proxy_enabled"]
            else "Proxy support: disabled"
        )
        if args.publish_only:
            startup_signal("Skipping repair loop; publish-only mode active.")
        elif args.ensure_validation_record:
            startup_signal("Skipping repair loop; validation-record mode active.")
        elif args.script_validate_only:
            startup_signal("Skipping repair loop; validation-only script mode active.")
        elif args.sync_conflicts:
            startup_signal("Skipping repair loop; conflict-sync mode active.")
        elif pattern_special_mode:
            startup_signal("Skipping repair loop; pattern-learning workflow active.")
        else:
            startup_signal("Starting repair loop...")
        print(f"Execution: {'remote' if target else 'local'}")
        if target:
            print(f"🌐 Target: {target}")
        print(f"Repository: {repo}")
        if CURRENT_VALIDATION_PLAN.get("active"):
            print(format_validation_plan_summary(CURRENT_VALIDATION_PLAN))
        print(f"selected pattern repo: {pattern_repo_selection.get('selected', 'none')}")
        print(f"pattern repo reason: {pattern_repo_selection.get('reason', '')}")
        print(f"pattern repo confidence: {pattern_repo_selection.get('confidence', 'low')}")
    if pattern_special_mode and not json_list_mode:
        print(f"Pattern repo: {pattern_repo if pattern_repo else 'none'}")
        if args.reset_pattern_repo:
            print("=== PATTERN REPO RESET ===")
            print(f"pattern_repo: {pattern_repo}")
            print(f"reset_existing: {format_bool(reset_existing)}")
            if not (
                args.import_pattern_files
                or args.learn_from
                or args.new_script
                or args.eval_pattern_learning
                or args.list_patterns
                or args.list_pattern_sources
                or args.relearn_patterns
                or args.forget_pattern
                or args.promote_pattern
                or args.demote_pattern
                or args.promote_source
                or args.demote_source
                or args.forget_source
                or args.add_to_training
            ):
                return
    should_sync_upstream = bool(
        not args.no_upstream_sync
        and not args.explain_only
        and (
            args.publish_only
            or publish_requested
            or not pattern_special_mode
            or args.learn_from
            or args.import_pattern_files
            or args.new_script
            or args.eval_pattern_learning
            or (args.script and args.add_to_training)
        )
    )
    upstream_sync_result = make_upstream_sync_result()
    if should_sync_upstream:
        upstream_sync_result = sync_with_upstream_before_workflow(
            repo,
            validation_command=args.test_cmd or latest_repo_validation_command(repo),
            target=target,
            no_auto_conflict_resolution_after_sync=bool(args.no_auto_conflict_resolution_after_sync),
            no_upstream_sync=bool(args.no_upstream_sync),
            force_upstream_merge=bool(args.force_upstream_merge),
        )
        if int((upstream_sync_result.get("behind_count") or 0)) > 0:
            print_upstream_change_analysis(upstream_sync_result.get("analysis") or {})
        print_upstream_sync_summary(upstream_sync_result)
        if upstream_sync_result.get("merge_conflict_result"):
            print_merge_conflict_summary(upstream_sync_result["merge_conflict_result"])
        if upstream_sync_result.get("sync_result") == "blocked":
            raise SystemExit(1)
    if args.import_pattern_files:
        imported = import_pattern_files(
            pattern_repo,
            args.import_pattern_files,
            trust_level=args.pattern_trust,
            tags=import_pattern_tags(args.pattern_tags),
            note=args.pattern_note or "",
        )
        print("=== PATTERN IMPORT ===")
        print(f"pattern_repo: {imported.get('pattern_repo', '')}")
        print(f"created_repo: {format_bool(imported.get('created_repo'))}")
        print(f"imported_count: {len(imported.get('imported_sources', []))}")
        for source in imported.get("imported_sources", []):
            print(
                f"- candidate_imported={format_bool(source.get('candidate_imported'))} "
                f"source_type={source.get('source_type', 'local')} source_origin={source.get('source_origin', source.get('origin_path', ''))} "
                f"acquisition_method={source.get('acquisition_method', 'direct')} proxy_used={format_bool(source.get('proxy_used'))} "
                f"source={source.get('candidate_path', '')} promoted_path={source.get('repo_rel_path', '')} "
                f"trust={source.get('trust_level', '')} tags={source.get('tags', [])} "
                f"sanitized={format_bool(source.get('sanitized_changed'))} "
                f"validated={format_bool(source.get('validation_passed'))} "
                f"repaired={format_bool(source.get('repair_needed'))} "
                f"promoted_to_training={format_bool(source.get('promoted'))}"
            )
        print(f"learned_pattern_count: {len(imported.get('learned_patterns', []))}")
        print(f"learned_pattern_delta: {imported.get('learned_pattern_delta', 0)}")
        print(f"relearn_triggered: {format_bool(imported.get('relearn_triggered'))}")
        return
    if args.import_pattern_repo:
        preview = scan_pattern_source_collection(
            Path(args.import_pattern_repo),
            include_globs=args.pattern_include,
            exclude_globs=args.pattern_exclude,
            max_files=max(0, int(args.pattern_max_files or DEFAULT_PATTERN_REPO_MAX_FILES)),
            max_depth=max(0, int(args.pattern_max_depth or 0)),
        )
        print_pattern_collection_preview(preview)
        if not preview.get("ok"):
            raise SystemExit(1)
        if args.explain_only:
            return
        imported = import_pattern_repo_collection(
            pattern_repo,
            args.import_pattern_repo,
            trust_level=args.pattern_trust,
            tags=import_pattern_tags(args.pattern_tags),
            note=args.pattern_note or "",
            include_globs=args.pattern_include,
            exclude_globs=args.pattern_exclude,
            max_files=max(0, int(args.pattern_max_files or DEFAULT_PATTERN_REPO_MAX_FILES)),
            max_depth=max(0, int(args.pattern_max_depth or 0)),
        )
        print("=== PATTERN REPO IMPORT ===")
        print(f"pattern_repo: {imported.get('pattern_repo', '')}")
        print(f"source_root: {imported.get('source_root', '')}")
        print(f"import_scope: {imported.get('import_scope', 'folder')}")
        print(f"candidate_count: {imported.get('candidate_count', 0)}")
        print(f"promoted_trusted_count: {imported.get('promoted_trusted_count', 0)}")
        print(f"promoted_experimental_count: {imported.get('promoted_experimental_count', 0)}")
        print(f"blocked_count: {imported.get('blocked_count', 0)}")
        print(f"repo_level_patterns_added: {imported.get('repo_level_patterns_added', 0)}")
        print(f"pattern_memory_delta: {imported.get('pattern_memory_delta', 0)}")
        for source in imported.get("imported_sources", []):
            print(
                f"- source_subpath={source.get('source_subpath', '')} "
                f"validation_status={source.get('validation_status', '')} "
                f"promotion_state={source.get('promotion_state_detail', source.get('promotion_state', 'candidate'))} "
                f"stored_path={source.get('repo_rel_path', '')}"
            )
        print(f"relearn_triggered: {format_bool(imported.get('relearn_triggered'))}")
        return
    if args.script and args.add_to_training:
        imported = import_pattern_files(
            pattern_repo,
            [args.script],
            trust_level=args.pattern_trust,
            tags=import_pattern_tags(args.pattern_tags),
            note=args.pattern_note or "",
        )
        print("=== SCRIPT TRAINING IMPORT ===")
        print(f"training_repo: {imported.get('pattern_repo', '')}")
        print(f"created_repo: {format_bool(imported.get('created_repo'))}")
        print(f"imported_count: {len(imported.get('imported_sources', []))}")
        for source in imported.get("imported_sources", []):
            print(
                f"- candidate_imported={format_bool(source.get('candidate_imported'))} "
                f"source_type={source.get('source_type', 'local')} source_origin={source.get('source_origin', source.get('origin_path', ''))} "
                f"acquisition_method={source.get('acquisition_method', 'direct')} proxy_used={format_bool(source.get('proxy_used'))} "
                f"source={source.get('candidate_path', '')} promoted_path={source.get('repo_rel_path', '')} "
                f"trust={source.get('trust_level', '')} sanitized={format_bool(source.get('sanitized_changed'))} "
                f"validated={format_bool(source.get('validation_passed'))} "
                f"repaired={format_bool(source.get('repair_needed'))} "
                f"promoted_to_training={format_bool(source.get('promoted'))}"
            )
        print(f"learned_pattern_delta: {imported.get('learned_pattern_delta', 0)}")
        print(f"relearn_triggered: {format_bool(imported.get('relearn_triggered'))}")
        return
    if args.relearn_patterns:
        relearned = relearn_patterns_from_repo(pattern_repo)
        print("=== PATTERN RELEARN ===")
        print(f"pattern_repo: {relearned.get('pattern_repo', '')}")
        print(f"learned_pattern_count: {len(relearned.get('learned_patterns', []))}")
        return
    if args.list_pattern_sources:
        inspection = inspect_pattern_sources(
            pattern_repo,
            limit=max(0, int(args.limit or 0)),
            filter_state=str(args.filter_state or ""),
            filter_tag=str(args.filter_tag or ""),
            search=str(args.search or ""),
        )
        if args.output == "json":
            print(json.dumps(inspection, indent=2, sort_keys=True))
        else:
            print("=== PATTERN SOURCES ===")
            print(f"pattern_repo: {inspection.get('pattern_repo', 'none')}")
            print_pattern_source_inspection(inspection.get("summary", {}), inspection.get("sources", []))
        return
    if args.list_patterns:
        inspection = inspect_patterns(
            pattern_repo,
            filter_state=str(args.filter_state or ""),
            filter_tag=str(args.filter_tag or ""),
            search=str(args.search or ""),
            limit=max(0, int(args.limit or 0)),
        )
        if args.output == "json":
            print(json.dumps(inspection, indent=2, sort_keys=True))
        else:
            print("=== PATTERNS ===")
            print(f"pattern_repo: {inspection.get('pattern_repo', 'none')}")
            print_pattern_inspection(
                inspection.get("summary", {}),
                inspection.get("patterns", []),
                show_promotion_state=bool(args.show_promotion_state),
            )
        return
    if args.promote_pattern:
        result = manage_pattern_state(
            pattern_repo,
            args.promote_pattern,
            action="promote",
            set_trust=str(args.set_trust or ""),
            set_promotion_state=str(args.set_promotion_state or ""),
            dry_run=bool(args.dry_run),
        )
        if args.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_pattern_control_result(result)
        return
    if args.demote_pattern:
        result = manage_pattern_state(
            pattern_repo,
            args.demote_pattern,
            action="demote",
            set_trust=str(args.set_trust or ""),
            set_promotion_state=str(args.set_promotion_state or ""),
            dry_run=bool(args.dry_run),
        )
        if args.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_pattern_control_result(result)
        return
    if args.promote_source:
        result = manage_source_state(
            pattern_repo,
            args.promote_source,
            action="promote",
            set_trust=str(args.set_trust or ""),
            set_promotion_state=str(args.set_promotion_state or ""),
            dry_run=bool(args.dry_run),
        )
        if args.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_pattern_control_result(result)
        return
    if args.demote_source:
        result = manage_source_state(
            pattern_repo,
            args.demote_source,
            action="demote",
            set_trust=str(args.set_trust or ""),
            set_promotion_state=str(args.set_promotion_state or ""),
            dry_run=bool(args.dry_run),
        )
        if args.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_pattern_control_result(result)
        return
    if args.forget_source:
        result = manage_source_state(
            pattern_repo,
            args.forget_source,
            action="forget",
            dry_run=bool(args.dry_run),
        )
        if args.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_pattern_control_result(result)
        return
    if args.forget_pattern:
        result = manage_pattern_state(
            pattern_repo,
            args.forget_pattern,
            action="forget",
            dry_run=bool(args.dry_run),
        )
        if args.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_pattern_control_result(result)
        return
    if args.learn_from:
        learned = learn_from_scripts(
            repo,
            args.learn_from,
            pattern_repo=pattern_repo,
            trust_level=args.pattern_trust,
            tags=import_pattern_tags(args.pattern_tags),
        )
        print("=== PATTERN LEARNING ===")
        print(f"learned_sources: {learned.get('learned_sources', [])}")
        print(f"learned_pattern_count: {len(learned.get('learned_patterns', []))}")
        print(f"pattern_memory_path: {pattern_repo_storage_path(pattern_repo, SCRIPT_PATTERN_MEMORY_FILE_NAME)}")
        for pattern in learned.get("learned_patterns", [])[:12]:
            print(
                f"- [{pattern.get('pattern_type')}] {pattern.get('summary')} "
                f"(source={pattern.get('source_repo_path', '')}; trust={pattern.get('trust_level', '')}; confidence={pattern.get('confidence', 0)})"
            )
        if not args.eval_pattern_learning and not args.new_script:
            return
    if args.new_script:
        output_path = Path(args.new_script)
        if not output_path.is_absolute():
            output_path = (repo / output_path).resolve()
        selection, pattern_repo_selection = resolve_pattern_selection(
            config_values,
            pattern_repo_selection,
            "new-script",
            args.new_script_purpose or output_path.stem,
            script_path=output_path,
        )
        prepared = prepare_new_script_generation(
            repo,
            output_path,
            args.new_script_purpose or output_path.stem,
            selection,
            pattern_repo_selection,
            probe_result=generation_probe_result,
            probe_url=args.probe_url or "",
            probe_type=args.probe_type,
            probe_headers=probe_headers,
            probe_bearer_token=args.probe_bearer_token or "",
            probe_cookie=args.probe_cookie or "",
            probe_user_agent=args.probe_user_agent or "",
            probe_method=args.probe_method or "",
            http_proxy=args.http_proxy or "",
            https_proxy=args.https_proxy or "",
            probe_timeout=max(1, int(args.probe_timeout or DEFAULT_PROBE_TIMEOUT_SECONDS)),
            probe_max_bytes=max(512, int(args.probe_max_bytes or DEFAULT_PROBE_MAX_BYTES)),
            probe_follow_up=max(0, int(args.probe_follow_up or DEFAULT_PROBE_FOLLOW_UP_LIMIT)),
        )
        rendered = prepared["rendered"]
        generation_plan = prepared["generation_plan"]
        plan = prepared["validation_plan"]
        chosen_plan = prepared["chosen_validation_plan"]
        chosen_plan = override_validation_stack_for_new_script(
            chosen_plan,
            output_path.parent,
            output_path,
            mode=args.new_script_validation_mode,
            custom_command=args.test_cmd or "",
        )
        generation_plan = finalize_new_script_generation_plan(generation_plan, chosen_plan, selection, pattern_repo_selection)
        if str(args.new_script_validation_mode or "auto") == "skip":
            validation_result = {"ok": True, "results": [], "failed_step": {}, "output": "", "failure_type": FAILURE_UNKNOWN, "skipped": True}
        else:
            validation_result = run_validation_stack(output_path.parent, chosen_plan)
        baseline_comparison = compare_pattern_baseline(plan, selection) if args.compare_pattern_baseline else {}
        if pattern_repo_selection.get("path"):
            learned_record = {
                "task_id": "live-new-script",
                "task_type": "new-script",
                "patterns_considered": [item.get("family", item.get("pattern_type", "")) for item in selection.get("considered", [])],
                "patterns_applied": [item.get("family", item.get("pattern_type", "")) for item in selection.get("applied", [])],
                "correctness_pass": bool(validation_result.get("ok")),
                "selection": selection,
                "score": {"total": 5 if validation_result.get("ok") else -5},
            }
            baseline_record = {
                "task_id": "live-new-script",
                "score": {"total": 0},
            }
            record_pattern_effectiveness(pattern_repo_selection.get("path"), [learned_record], [baseline_record])
        print("=== NEW SCRIPT RESULT ===")
        probe_findings_text = "; ".join(list(generation_plan.get("probe_summary", {}).get("key_findings") or [])[:3])
        patterns_applied = [item.get("pattern_type", "") for item in selection.get("applied", []) if item.get("pattern_type")]
        print(f"script_generated: {format_bool(True)}")
        print(f"path: {output_path}")
        print(f"output_path: {output_path}")
        print(f"pattern_source_used: {generation_plan.get('pattern_source_used', 'none')}")
        print(f"patterns_applied: {patterns_applied}")
        print(f"probe_used: {format_bool(bool(generation_plan.get('probe_used')))}")
        print(f"key_probe_findings: {probe_findings_text or '(none)'}")
        print(f"validation_plan: {generation_plan.get('validation_plan') or chosen_plan.get('primary_command', '') or '(none)'}")
        print(f"generation_confidence: {generation_plan.get('generation_confidence', 'low')}")
        if generation_plan.get("confidence_reasons"):
            print(f"generation_confidence_reasoning: {'; '.join(list(generation_plan.get('confidence_reasons') or [])[:4])}")
        print(format_script_pattern_transparency(selection))
        print(f"validation_command: {chosen_plan.get('primary_command', '')}")
        print(f"validation_result: {'skipped' if validation_result.get('skipped') else ('success' if validation_result.get('ok') else 'blocked')}")
        print(f"validation_success: {validation_result.get('ok')}")
        print(f"script_kind: {rendered.get('script_kind', generation_plan.get('script_kind', 'local'))}")
        if generation_plan.get("proposed_script_structure"):
            print(f"proposed_script_structure: {generation_plan.get('proposed_script_structure')}")
        plain_summary = "Generated a local script using learned patterns."
        if rendered.get("script_kind") == "api":
            plain_summary = "Generated a proxy-aware API script using learned patterns and live endpoint evidence."
        elif rendered.get("script_kind") == "m3u8":
            plain_summary = "Generated an HLS/M3U8 utility using learned patterns and live playlist inspection."
        elif patterns_applied:
            plain_summary = "Generated a local-only script using trusted learned patterns."
        if validation_result.get("skipped"):
            plain_summary = "The new script was created locally, but validation was skipped."
        elif not validation_result.get("ok"):
            plain_summary = "Generated the script, but validation did not pass yet."
        print(f"what_happened: {plain_summary}")
        if args.compare_pattern_baseline:
            print("=== PATTERN BASELINE COMPARISON ===")
            print(f"baseline_validation_command: {baseline_comparison.get('baseline_validation_command', '')}")
            print(f"learned_validation_command: {baseline_comparison.get('learned_validation_command', '')}")
            print(f"patterns_added_by_learning: {baseline_comparison.get('patterns_added', [])}")
            print(f"learned_path_improved_fit: {format_bool(baseline_comparison.get('improved_fit'))}")
        if validation_result.get("output"):
            print(f"validation_output: {validation_result.get('output')}")
        if not validation_result.get("ok") and not validation_result.get("skipped") and not args.eval_pattern_learning:
            raise SystemExit(1)
        if not args.eval_pattern_learning:
            return
    if args.eval_pattern_learning:
        eval_result = run_pattern_learning_eval(repo, args.pattern_eval_tasks, pattern_repo=pattern_repo)
        print_pattern_eval_report(eval_result)
        return
    if args.ensure_validation_record:
        validation_record_result = ensure_validation_record_for_current_commit(
            repo,
            validation_command=args.test_cmd or "",
            target=target,
        )
        print_validation_record_result(validation_record_result)
        if not validation_record_result.get("ok"):
            raise SystemExit(1)
        return
    if args.script_validate_only:
        if not args.script:
            raise SystemExit("--script-validate-only requires --script")
        script_path = Path(args.script).expanduser()
        if not script_path.is_absolute():
            script_path = (repo / script_path).resolve()
        plan = build_script_validation_plan(repo, script_path)
        if args.test_cmd:
            plan["primary_command"] = args.test_cmd
            plan["chosen_stack"] = [
                {"command": f"python -m py_compile {shlex.quote(plan.get('script_rel_path') or relative_script_path(repo, script_path))}", "kind": "syntax"},
                {"command": args.test_cmd, "kind": "custom"},
            ]
            plan["candidates"] = [{"command": args.test_cmd, "kind": "custom", "confidence": 1.0, "source": "interactive"}]
        print(format_validation_plan_summary(plan))
        validation_run = run_validation_stack(repo, plan)
        print_script_validation_only_result(script_path, plan, validation_run)
        if not validation_run.get("ok"):
            raise SystemExit(1)
        return
    if args.sync_conflicts:
        merge_conflict_outcome = maybe_handle_merge_conflicts(
            repo,
            validation_command=args.test_cmd or latest_repo_validation_command(repo),
            publish_requested=False,
            publish_mode="sync-only",
            publish_branch=args.publish_branch or "",
            publish_pr=False,
            publish_merge=False,
            publish_merge_local_main=False,
            publish_message=args.publish_message or "",
            target=target,
            dry_run_mode=args.dry_run,
        force_publish=False,
        auto_stage_safe_paths=not bool(args.no_auto_stage),
        auto_remediate_blockers=not bool(args.no_auto_remediate_blockers),
        explain_staging=bool(args.explain_staging),
        no_auto_merge_conflicts=bool(args.no_auto_merge_conflicts),
        )
        if merge_conflict_outcome and not merge_conflict_outcome.get("success"):
            raise SystemExit(1)
        if not merge_conflict_outcome:
            print("FINAL: no merge conflicts detected")
        return
    if args.publish_only:
        print("Active mode: publish current repo state")
    elif publish_requested:
        print("Active mode: publish validated run")
    if args.explain_only:
        print("Explain-only mode: resolved settings only, no repair attempt will run.")
        print(format_run_artifact_summary(repo, recent_state_path, config_path))
        return
    merge_conflict_outcome = maybe_handle_merge_conflicts(
        repo,
        validation_command=args.test_cmd or latest_repo_validation_command(repo),
        publish_requested=bool(args.publish_only or publish_requested),
        publish_mode="current-repo-state" if args.publish_only else "validated-run",
        publish_branch=args.publish_branch or "",
        publish_pr=args.publish_pr,
        publish_merge=args.publish_merge,
        publish_merge_local_main=args.publish_merge_local_main,
        publish_message=args.publish_message or "",
        target=target,
        dry_run_mode=args.dry_run,
        force_publish=bool(args.force_publish),
        auto_stage_safe_paths=not bool(args.no_auto_stage),
        auto_remediate_blockers=not bool(args.no_auto_remediate_blockers),
        explain_staging=bool(args.explain_staging),
        no_auto_merge_conflicts=bool(args.no_auto_merge_conflicts),
    )
    if merge_conflict_outcome:
        if merge_conflict_outcome.get("success"):
            return
        if not merge_conflict_outcome.get("continue_with_repair"):
            raise SystemExit(1)
    if args.publish_only:
        validation_state = resolve_publish_validation_state(repo)
        validation_state = attempt_publish_auto_revalidation(
            repo,
            validation_state,
            no_auto_revalidate=bool(args.no_auto_revalidate),
        )
        publish_result = publish_current_repo_state(
            repo,
            args.publish_branch or "",
            args.publish_pr,
            args.publish_merge,
            args.publish_merge_local_main,
            args.publish_message or "",
            target,
            args.dry_run,
            validation_state=str(validation_state.get("validation_state") or "blocked"),
            validation_detail=str(validation_state.get("reason") or ""),
            force_publish=bool(args.force_publish),
            validation_commit_match=bool(validation_state.get("validation_commit_match")),
            fingerprint_match=bool(validation_state.get("fingerprint_match")),
            last_validated_commit=str(validation_state.get("last_validated_commit") or ""),
            current_commit=str(validation_state.get("current_commit") or ""),
            validation_age_seconds=int(validation_state.get("validation_age_seconds", -1)),
            auto_revalidated=bool(validation_state.get("auto_revalidated")),
            validation_reused=bool(validation_state.get("validation_reused")),
            auto_revalidation_result=str(validation_state.get("auto_revalidation_result") or "not_needed"),
            auto_stage_safe_paths=not bool(args.no_auto_stage),
            auto_remediate_blockers=not bool(args.no_auto_remediate_blockers),
            explain_staging=bool(args.explain_staging),
        )
        docs_reporting = summarize_docs_publish_reporting(
            docs_check_performed=bool(
                publish_result.get("docs_check_performed", publish_result.get("docs_checked_at_publish"))
            ),
            docs_required=bool(publish_result.get("docs_required")),
            docs_updated=bool(publish_result.get("docs_updated")),
            blocked=bool(
                publish_result.get("control_path") == "blocked_docs"
                or (
                    bool(publish_result.get("docs_required"))
                    and not bool(publish_result.get("docs_updated"))
                    and bool(publish_result.get("reason"))
                )
            ),
            reason=str(publish_result.get("docs_reason") or publish_result.get("reason") or ""),
        )
        publish_summary = {
            "validation_state": str(validation_state.get("validation_state") or "blocked"),
            "validation_result": str(validation_state.get("validation_result") or "blocked"),
            "validation_commit_match": bool(validation_state.get("validation_commit_match")),
            "fingerprint_match": bool(validation_state.get("fingerprint_match")),
            "auto_revalidated": bool(validation_state.get("auto_revalidated")),
            "validation_reused": bool(validation_state.get("validation_reused")),
            "auto_revalidation_result": str(validation_state.get("auto_revalidation_result") or "not_needed"),
            "last_validated_commit": str(validation_state.get("last_validated_commit") or ""),
            "current_commit": str(validation_state.get("current_commit") or ""),
            "validation_age_seconds": int(validation_state.get("validation_age_seconds", -1)),
            "publish_requested": True,
            "publish_triggered": bool(publish_result.get("triggered")),
            "publish_mode": "current-repo-state",
            "publish_result": (publish_result.get("final") or {}).get("status") or "failed",
            "publish_result_detail": publish_result,
            "publish_reason": publish_result.get("publish_reason") or "",
            "publish_detail_reason": publish_result.get("reason") or (publish_result.get("verification") or {}).get("reason") or "",
            "pr_created_or_reused": bool(publish_result.get("pr_created_or_reused") or publish_result.get("pr_already_exists")),
            "pr_url": publish_result.get("pr_url") or "",
            "pr_summary": publish_result.get("pr_summary") or "",
            "pr_merged": bool(publish_result.get("pr_merged")),
            "local_main_synced": bool(publish_result.get("local_main_synced")),
            "docs_checked_at_publish": bool(publish_result.get("docs_checked_at_publish")),
            "docs_check_performed": bool(docs_reporting.get("docs_check_performed")),
            "docs_status": docs_reporting.get("docs_status") or "up_to_date",
            "docs_reason": docs_reporting.get("docs_reason") or "documentation check not performed",
            "docs_required": bool(publish_result.get("docs_required")),
            "docs_updated": bool(publish_result.get("docs_updated")),
            "docs_refresh_mode": publish_result.get("docs_refresh_mode") or "none",
            "docs_targets": publish_result.get("docs_targets") or [],
            "auto_stage_attempted": bool(publish_result.get("auto_stage_attempted")),
            "auto_stage_result": publish_result.get("auto_stage_result") or "not_needed",
            "auto_staged_paths": publish_result.get("auto_staged_paths") or [],
            "remaining_unstaged_paths": publish_result.get("remaining_unstaged_paths") or [],
            "remaining_unstaged": publish_result.get("remaining_unstaged") or [],
            "working_tree_clean": bool(publish_result.get("working_tree_clean", True)),
            "working_tree_publishable_paths": publish_result.get("working_tree_publishable_paths") or [],
            "working_tree_internal_paths": publish_result.get("working_tree_internal_paths") or [],
            "working_tree_artifact_paths": publish_result.get("working_tree_artifact_paths") or [],
            "file_decisions": publish_result.get("file_decisions") or [],
            "staging_summary": publish_result.get("staging_summary") or {"auto_staged": 0, "ignored": 0, "blocked": 0},
            "staging_decision_reason": publish_result.get("staging_decision_reason") or "",
            "staging_reason": publish_result.get("staging_reason") or "",
            "explain_staging": bool(publish_result.get("explain_staging")),
            "meaningful_changes_detected": bool(publish_result.get("meaningful_changes_detected")),
            "last_published_commit": publish_result.get("last_published_commit") or "",
            "current_publish_candidate_commit": publish_result.get("current_publish_candidate_commit") or "",
            "diff_files_detected": publish_result.get("diff_files_detected") or [],
            "meaningful_paths": publish_result.get("meaningful_paths") or [],
            "ignored_changes": publish_result.get("ignored_changes") or [],
            "previous_publish_branch": publish_result.get("previous_publish_branch") or "",
            "previous_pr_url": publish_result.get("previous_pr_url") or "",
            "previous_commit": publish_result.get("previous_commit") or "",
            "base_branch": publish_result.get("base_branch") or "",
            "prepublish_base_alignment_attempted": bool(publish_result.get("prepublish_base_alignment_attempted")),
            "branch_diverged": bool(publish_result.get("branch_diverged")),
            "alignment_needed": bool(publish_result.get("alignment_needed")),
            "alignment_result": publish_result.get("alignment_result") or "not_needed",
            "alignment_changed_commit": bool(publish_result.get("alignment_changed_commit")),
            "validation_rerun_after_alignment": bool(publish_result.get("validation_rerun_after_alignment")),
            "alignment_block_reason": publish_result.get("alignment_block_reason") or "",
            "final_workflow_result": publish_result.get("final_workflow_result") or ((publish_result.get("final") or {}).get("status") or "failed"),
            "pr_mergeable": publish_result.get("pr_mergeable") or "unknown",
            "pr_conflicts_detected": bool(publish_result.get("pr_conflicts_detected")),
            "pr_mergeability_reason": publish_result.get("pr_mergeability_reason") or "",
            "pr_mergeability_source": publish_result.get("pr_mergeability_source") or "github",
            "pr_mergeable_final": publish_result.get("pr_mergeable_final") or (publish_result.get("pr_mergeable") or "unknown"),
            "pr_conflicts_detected_final": bool(publish_result.get("pr_conflicts_detected_final")),
            "pr_mergeability_repair_attempted": bool(publish_result.get("pr_mergeability_repair_attempted")),
            "pr_mergeability_repair_result": publish_result.get("pr_mergeability_repair_result") or "not_needed",
        }
        print_post_success_publish_summary(publish_summary)
        print_publish_summary(publish_result)
        if publish_result.get("merge_conflict_result"):
            print_merge_conflict_summary(publish_result["merge_conflict_result"])
        print(format_final_operator_summary(publish_summary))
        if publish_summary_requires_failure(publish_summary):
            raise SystemExit(1)
        return
    if target:
        ok, error = ensure_remote_session()
        if not ok:
            blocked = REMOTE_EXECUTION_STATE.get("blocked") or {
                "kind": "connectivity issue",
                "reason": "remote SSH session could not be established",
                "evidence": error[:500] if error else f"Could not establish SSH session to {target}",
                "needs": f"reachable SSH access to {target}",
                "action": f"Verify `ssh {target}` and confirm the remote host is reachable.",
            }
            print(format_blocked_message(blocked))
            early_metrics = {
                "repo": str(repo),
                "success": False,
                "total_attempts": 0,
                "attempts_to_success": None,
                "rollback_count": 0,
                "remote_blocked_kind": blocked.get("kind"),
                "blocked_reason": blocked.get("reason"),
                "proxy_enabled": API_SAFETY_STATE["proxy_enabled"],
                "likely_rate_limit_hits": API_SAFETY_STATE["likely_rate_limit_hits"],
                "cooldowns_triggered": API_SAFETY_STATE["cooldowns_triggered"],
                "attempts": [],
            }
            print(summarize_run_metrics(early_metrics))
            append_run_metrics(repo, early_metrics)
            raise SystemExit(1)
        repo_ok, repo_blocked = check_remote_repo_access(repo)
        if not repo_ok:
            blocked = repo_blocked or {
                "kind": "repo/path issue",
                "reason": "remote repo path not found",
                "evidence": f"{repo} was not accessible on {target}",
                "needs": f"an existing accessible repo at {repo} on {target}",
                "action": f"Confirm the repo path on {target} and rerun with `--repo {repo}`.",
            }
            print(format_blocked_message(blocked))
            early_metrics = {
                "repo": str(repo),
                "success": False,
                "total_attempts": 0,
                "attempts_to_success": None,
                "rollback_count": 0,
                "remote_blocked_kind": blocked.get("kind"),
                "blocked_reason": blocked.get("reason"),
                "proxy_enabled": API_SAFETY_STATE["proxy_enabled"],
                "likely_rate_limit_hits": API_SAFETY_STATE["likely_rate_limit_hits"],
                "cooldowns_triggered": API_SAFETY_STATE["cooldowns_triggered"],
                "attempts": [],
            }
            print(summarize_run_metrics(early_metrics))
            append_run_metrics(repo, early_metrics)
            raise SystemExit(1)

    branch_name = ensure_branch_per_run(repo)
    pattern_memory = load_pattern_memory(repo)
    artifact_dir = create_run_artifact_dir(repo)
    publish_baseline_paths = meaningful_changed_paths(repo) if publish_requested else []

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(STRATEGY_MINIMAL_PATCH, FAILURE_UNKNOWN, None, None, None, None, None, None),
        },
        {
            "role": "user",
            "content": build_user_prompt(
                repo,
                branch_name,
                args.test_cmd,
                STRATEGY_MINIMAL_PATCH,
                FAILURE_UNKNOWN,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        },
    ]

    attempt_notes = []
    latest_test_passed = False
    latest_failed_test_output = ""
    pending_modified_files = set()
    last_failed_attempt_files: list[str] = []
    auto_restored_files: list[str] = []
    last_failure_signature = ""
    repeated_failure_count = 0
    failed_test_runs = 0
    failure_type = FAILURE_UNKNOWN
    previous_failure_type = FAILURE_UNKNOWN
    previous_failure_count: int | None = None
    strategy_mode = STRATEGY_MINIMAL_PATCH
    require_diff_before_write = False
    diff_seen_since_failure = True
    read_test_files_since_failure: set[str] = set()
    read_target_files_since_failure: set[str] = set()
    diagnosis_explanation = ""
    diff_reasoning = ""
    last_diff_output = ""
    require_diff_reasoning = False
    edit_plan = ""
    edit_scope = ""
    test_expectation = ""
    test_alignment = ""
    selected_relevant_files = []
    required_test_files: list[str] = []
    required_impl_files: list[str] = []
    required_traceback_files: list[str] = []
    primary_relevant_file = ""
    read_files_since_failure: set[str] = set()
    search_count_this_attempt = 0
    search_terms_this_attempt: set[str] = set()
    search_hit_files: set[str] = set()
    search_required_this_attempt = False
    search_trigger_reasons: list[str] = []
    suggested_search_terms: list[str] = []
    precision_patch = {"active": False, "file": "", "symbol": "", "reason": ""}
    attempt_history: list[dict] = []
    diversification = {"preferred": "", "avoid": "", "reason": ""}
    current_hypothesis = {"text": "", "symbols": [], "files": []}
    current_plan = {"steps": [], "files": [], "symbols": [], "current_step_index": 0}
    current_memory_hint = {
        "matched": False,
        "score": 0,
        "successful_strategy": "",
        "successful_files": [],
        "successful_symbols": [],
        "failed_strategies": [],
    }
    repair_context = {}
    current_failure_context = {
        "failing_test_name": "",
        "failing_assertion": "",
        "expected_value": "",
        "actual_value": "",
        "stack_frames": [],
    }
    run_metrics = {
        "repo": str(repo),
        "success": False,
        "total_attempts": 0,
        "attempts_to_success": None,
        "rollback_count": 0,
        "remote_blocked_kind": None,
        "blocked_reason": None,
        "proxy_enabled": API_SAFETY_STATE["proxy_enabled"],
        "likely_rate_limit_hits": 0,
        "cooldowns_triggered": 0,
        "attempts": [],
    }
    last_attempt_score = 0
    zero_score_streak = 0
    recent_scores: list[int] = []
    score_escalation_pressure = 0
    material_regression = False
    repeated_regression_count = 0
    precommit_rejection_count = 0
    best_attempt: dict | None = None
    stronger_inspection_required = False

    if getattr(args, "repair_context_file", ""):
        try:
            repair_context_path = Path(str(args.repair_context_file)).expanduser()
            repair_context = json.loads(repair_context_path.read_text())
        except (OSError, json.JSONDecodeError):
            repair_context = {}
        if repair_context.get("repair_context_used"):
            failure_type = validation_error_type_to_failure_type(str(repair_context.get("validation_error_type") or ""))
            current_failure_context = repair_context_as_failure_context(repair_context)
            required_test_files = list(repair_context.get("failing_test_files") or [])[:1]
            required_impl_files = list(repair_context.get("failing_source_files") or repair_context.get("repair_targets") or [])[:1]
            required_traceback_files = [frame.get("path", "") for frame in current_failure_context.get("stack_frames", []) if frame.get("path")]
            primary_relevant_file = str(
                (repair_context.get("repair_targets") or repair_context.get("failing_source_files") or [""])[0] or ""
            )
            selected_relevant_files = [
                {"path": path, "score": 10 - index, "reasons": ["publish validation failure analysis"]}
                for index, path in enumerate(repair_context.get("repair_targets") or repair_context.get("failing_source_files") or [])
                if path
            ][:5]
            if primary_relevant_file:
                precision_patch = {
                    "active": True,
                    "file": primary_relevant_file,
                    "symbol": str((repair_context.get("precision_patch") or {}).get("symbol") or ""),
                    "reason": "seeded from publish validation failure analysis",
                }
            current_hypothesis = {
                "text": str(repair_context.get("repair_goal") or ""),
                "symbols": [str((repair_context.get("precision_patch") or {}).get("symbol") or "")] if (repair_context.get("precision_patch") or {}).get("symbol") else [],
                "files": [primary_relevant_file] if primary_relevant_file else [],
            }
            current_plan = build_attempt_plan(
                {
                    "selected": selected_relevant_files,
                    "required_test_files": required_test_files,
                    "required_impl_files": required_impl_files,
                    "required_traceback_files": required_traceback_files,
                    "primary_file": primary_relevant_file,
                },
                precision_patch,
                current_failure_context,
            )

    for step in range(1, args.max_steps + 1):
        print(f"\n=== AGENT STEP {step} ===")
        API_SAFETY_STATE["attempt_rate_limit_hits"] = 0
        progress(f"starting attempt {step}...")
        strategy_mode = determine_strategy_mode(
            failed_test_runs,
            repeated_failure_count,
            failure_type,
            score_escalation_pressure,
        )
        messages[0]["content"] = build_system_prompt(
            strategy_mode,
            failure_type,
            precision_patch,
            diversification,
            current_hypothesis,
            current_plan,
            current_memory_hint,
            repair_context,
        )
        messages[1]["content"] = build_user_prompt(
            repo,
            branch_name,
            args.test_cmd,
            strategy_mode,
            failure_type,
            precision_patch,
            diversification,
            current_hypothesis,
            current_plan,
            current_memory_hint,
            repair_context,
        )
        print(f"Active strategy mode: {strategy_mode}")
        print(f"Detected failure type: {failure_type}")
        if repair_context.get("repair_context_used") and step == 1:
            print(format_validation_failure_analysis(repair_context))
        if current_failure_context.get("failing_test_name"):
            print(format_failure_context(current_failure_context))
        if current_hypothesis.get("text"):
            progress(f"hypothesis: {current_hypothesis['text'][:160]}")
        if current_plan.get("steps"):
            print("Plan:")
            for idx, plan_step in enumerate(current_plan["steps"], start=1):
                print(f"{idx}. {plan_step}")
            print(f"Executing plan step: {current_plan.get('current_step_index', 0) + 1}")
        if diversification.get("reason"):
            print(f"Strategy diversification: {diversification['reason']}")
        if current_memory_hint.get("matched"):
            print("Loaded prior pattern")
            if current_memory_hint.get("successful_strategy"):
                print(f"Reusing successful strategy: {current_memory_hint['successful_strategy']}")
            if current_memory_hint.get("failed_strategies"):
                print(
                    "Avoiding known failed strategy: "
                    + ", ".join(current_memory_hint.get("failed_strategies", [])[:3])
                )

        resp = call_model(messages, tools=TOOLS, tool_choice="auto", max_tokens=1400)
        msg = resp.choices[0].message

        if msg.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            ran_tests = False
            latest_test_output = ""
            tests_passed = False

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = tc.function.arguments or "{}"

                print(f"Tool call: {tool_name}({tool_args})")
                write_error = validate_write_request(
                    repo,
                    tool_name,
                    tool_args,
                    strategy_mode,
                    require_diff_before_write,
                    diff_seen_since_failure,
                    read_test_files_since_failure,
                    read_target_files_since_failure,
                    diagnosis_explanation,
                    diff_reasoning,
                    require_diff_reasoning,
                    edit_plan,
                    edit_scope,
                    test_expectation,
                    test_alignment,
                    required_test_files,
                    required_impl_files,
                    required_traceback_files,
                    read_files_since_failure,
                    search_required_this_attempt,
                    search_count_this_attempt,
                    search_trigger_reasons,
                    suggested_search_terms,
                    precision_patch,
                )
                if write_error is not None:
                    result = write_error
                elif tool_name == "search_repo":
                    try:
                        search_args = json.loads(tool_args) if tool_args else {}
                    except json.JSONDecodeError:
                        search_args = {}
                    term = (search_args.get("term") or "").strip()
                    if not term:
                        result = json.dumps({"ok": False, "error": "Search term is required."}, indent=2)
                    elif term in search_terms_this_attempt:
                        result = json.dumps(
                            {"ok": False, "error": f"Repeated search not allowed in this attempt: {term}"},
                            indent=2,
                        )
                    elif search_count_this_attempt >= MAX_SEARCHES_PER_ATTEMPT:
                        result = json.dumps({"ok": False, "error": "Search limit reached for this attempt."}, indent=2)
                    else:
                        reason_text = "; ".join(search_trigger_reasons) or "gathering additional repository context"
                        print(f"Search triggered: {reason_text}")
                        print(f"Search term: {term}")
                        search_terms_this_attempt.add(term)
                        search_count_this_attempt += 1
                        result = handle_tool(repo, args.max_file_chars, tool_name, tool_args, latest_test_passed)
                else:
                    result = handle_tool(repo, args.max_file_chars, tool_name, tool_args, latest_test_passed)
                print("Tool result:")
                print(result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

                modified_path = should_track_modified_file(tool_name, result)
                if modified_path:
                    pending_modified_files.add(modified_path)
                if tool_name == "git_restore_file":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    restored_path = data.get("path")
                    if data.get("ok") is True and isinstance(restored_path, str):
                        pending_modified_files.discard(restored_path)
                if tool_name == "git_restore_all":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("ok") is True:
                        pending_modified_files.clear()
                if tool_name == "git_diff":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("ok") is True:
                        diff_seen_since_failure = True
                        last_diff_output = data.get("output", "") if isinstance(data.get("output"), str) else ""
                if tool_name == "read_file":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    read_path = data.get("path")
                    if data.get("ok") is True and isinstance(read_path, str):
                        read_files_since_failure.add(read_path)
                        if is_test_file_path(read_path):
                            read_test_files_since_failure.add(read_path)
                        else:
                            read_target_files_since_failure.add(read_path)
                if tool_name == "search_repo":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("ok") is True:
                        search_required_this_attempt = False
                        for path in data.get("files", []):
                            if isinstance(path, str):
                                search_hit_files.add(path)
                        if latest_failed_test_output:
                            relevant_context = extract_relevant_file_context(
                                repo,
                                latest_failed_test_output,
                                last_failed_attempt_files,
                                sorted(search_hit_files),
                                current_memory_hint,
                            )
                            selected_relevant_files = relevant_context["selected"]
                            required_test_files = relevant_context["required_test_files"]
                            required_impl_files = relevant_context["required_impl_files"]
                            required_traceback_files = relevant_context["required_traceback_files"]
                            primary_relevant_file = relevant_context["primary_file"]
                            refreshed_failure_context = extract_failure_context(latest_failed_test_output, repo)
                            precision_patch = extract_precision_patch_context(
                                latest_failed_test_output,
                                relevant_context,
                                refreshed_failure_context,
                            )
                            if selected_relevant_files:
                                print("\n=== RELEVANT FILES (REFRESHED FROM SEARCH) ===")
                                print(format_relevant_file_context(relevant_context))
                            if precision_patch.get("active"):
                                print(
                                    "Precision patch mode active: "
                                    f"file={precision_patch['file']} symbol={precision_patch.get('symbol') or '(localized block)'}"
                                )

                if tool_name == "run_shell":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {"ok": False, "output": result, "command": ""}

                    if data.get("command") == args.test_cmd:
                        ran_tests = True
                        latest_test_output = data.get("output", "")
                        tests_passed = data.get("ok") is True
                        latest_test_passed = tests_passed

            if ran_tests and tests_passed:
                if current_failure_context.get("failing_test_name"):
                    print(f"Per-test improvement status: {evaluate_test_progress(current_failure_context, {}, True)}")
                finalization = finalize_success(
                    repo,
                    args.max_file_chars,
                    messages,
                    latest_test_passed,
                    step,
                    args.test_cmd,
                    current_failure_context,
                    primary_relevant_file,
                    best_attempt,
                    current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                    args.dry_run,
                    args.show_diff,
                    resolved_mode,
                    publish_requested,
                    args.publish_message or "",
                )
                if finalization.get("rejected"):
                    precommit_rejection_count += 1
                    last_attempt_score = -3
                    recent_scores.append(-3)
                    if len(recent_scores) > RECENT_SCORE_HISTORY:
                        recent_scores = recent_scores[-RECENT_SCORE_HISTORY:]
                    run_metrics["attempts"].append(
                        {
                            "step": step,
                            "strategy_type": current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                            "hypothesis_result": "rejected",
                            "candidate_scores": [item.get("candidate_score", 0) for item in finalization.get("candidate_results", [])],
                            "candidate_count": len(finalization.get("candidate_results", [])),
                            "candidate_rejections": sum(1 for item in finalization.get("candidate_results", []) if not item.get("ok")),
                            "validation_outcomes": [item.get("status", "") for item in finalization.get("candidate_results", [])],
                        }
                    )
                    attempt_notes.append(
                        "Pre-commit validation failed:\n"
                        f"- status: {finalization.get('status', 'rejected')}\n"
                        f"- failure_type: {finalization.get('failure_type', FAILURE_UNKNOWN)}\n"
                        f"- output: {str(finalization.get('output', ''))[:1500]}"
                    )
                    if precommit_rejection_count >= 2:
                        diversification = {
                            "preferred": "",
                            "avoid": "",
                            "reason": "Pre-commit validation has rejected patches repeatedly. Change approach before attempting another commit.",
                        }
                        blocked = detect_blocked_state(
                            failure_type,
                            str(finalization.get("output", "")),
                            {"selected": [{"score": 10}]},
                            precommit_rejection_count,
                            repeated_failure_count,
                            zero_score_streak,
                            API_SAFETY_STATE["likely_rate_limit_hits"],
                        )
                        if blocked:
                            run_metrics["blocked_reason"] = blocked["reason"]
                            run_metrics["remote_blocked_kind"] = blocked.get("kind")
                            print(format_blocked_message(blocked))
                    for candidate_result in finalization.get("candidate_results", []):
                        if not candidate_result.get("ok"):
                            pattern_memory = update_pattern_memory(
                                pattern_memory,
                                build_failure_signature(failure_type, current_failure_context, precision_patch),
                                candidate_result.get("strategy_type", ""),
                                "rejected",
                                current_hypothesis.get("files", []),
                                current_hypothesis.get("symbols", []),
                            )
                    save_pattern_memory(repo, pattern_memory)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Pre-commit validation failed and the patch was rejected. "
                                "Treat this as a failed attempt with a strong negative signal, "
                                "change strategy, and do not try to commit until validation passes."
                            ),
                        }
                    )
                    latest_test_passed = False
                    continue
                chosen_candidate_name = finalization.get("chosen_candidate", "")
                if chosen_candidate_name:
                    print(f"chosen candidate {chosen_candidate_name}")
                run_metrics["attempts"].append(
                    {
                        "step": step,
                        "strategy_type": current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                        "hypothesis_result": "confirmed",
                        "candidate_scores": [item.get("candidate_score", 0) for item in finalization.get("candidate_results", [])],
                        "candidate_count": len(finalization.get("candidate_results", [])),
                        "candidate_rejections": sum(1 for item in finalization.get("candidate_results", []) if not item.get("ok")),
                        "validation_outcomes": [item.get("status", "") for item in finalization.get("candidate_results", [])],
                    }
                )
                pattern_memory = update_pattern_memory(
                    pattern_memory,
                    build_failure_signature(failure_type, current_failure_context, precision_patch),
                    current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                    "confirmed",
                    current_hypothesis.get("files", []),
                    current_hypothesis.get("symbols", []),
                )
                save_pattern_memory(repo, pattern_memory)
                run_metrics["success"] = True
                run_metrics["attempts_to_success"] = step
                run_metrics["total_attempts"] = max(run_metrics["total_attempts"], step)
                run_metrics["likely_rate_limit_hits"] = API_SAFETY_STATE["likely_rate_limit_hits"]
                run_metrics["cooldowns_triggered"] = API_SAFETY_STATE["cooldowns_triggered"]
                summary_text = summarize_run_metrics(run_metrics)
                print(summary_text)
                append_run_metrics(repo, run_metrics)
                comparison_text = analyze_run_comparison(load_recent_run_metrics(repo))
                outcome_label = "dry-run" if args.dry_run else "success"
                action_text, rerun_cmd, continue_cmd, full_suite_cmd = build_action_summary(repo, args.test_cmd, resolved_mode, outcome_label, args.dry_run)
                write_run_artifacts(repo, artifact_dir, run_metrics, summary_text, comparison_text, rerun_cmd, continue_cmd, full_suite_cmd)
                update_recent_state(
                    repo,
                    args.test_cmd,
                    resolved_mode,
                    "success",
                    artifact_dir,
                    target,
                    files_changed=finalization.get("changed_paths", []),
                    confidence=str(finalization.get("confidence_level", "") or ""),
                    blocked_reason=str(run_metrics.get("blocked_reason") or ""),
                )
                print(comparison_text)
                print(action_text)
                print(format_run_artifact_summary(repo, recent_state_path, config_path, artifact_dir, target))
                if args.no_finalize:
                    fail_incomplete_without_finalization()
                publish_summary = run_post_success_publish(
                    repo,
                    args.test_cmd,
                    step,
                    finalization.get("confidence_level", ""),
                    artifact_dir,
                    finalization.get("changed_paths", []),
                    args.publish_branch or "",
                    args.publish_pr,
                    args.publish_merge,
                    args.publish_merge_local_main,
                    args.publish_message or "",
                    target,
                    run_metrics.get("blocked_reason"),
                    publish_baseline_paths,
                    args.dry_run,
                    "validated-run",
                    True,
                    publish_requested,
                    auto_stage_safe_paths=not bool(args.no_auto_stage),
                    auto_remediate_blockers=not bool(args.no_auto_remediate_blockers),
                    explain_staging=bool(args.explain_staging),
                )
                print_post_success_publish_summary(publish_summary)
                if publish_summary.get("publish_result_detail"):
                    print_publish_summary(publish_summary["publish_result_detail"])
                    if publish_summary["publish_result_detail"].get("merge_conflict_result"):
                        print_merge_conflict_summary(publish_summary["publish_result_detail"]["merge_conflict_result"])
                print(format_final_operator_summary(publish_summary))
                if publish_summary_requires_failure(publish_summary):
                    print("\nFailed: validation succeeded but publish did not complete successfully.", file=sys.stderr)
                    raise SystemExit(1)
                return

            if ran_tests and latest_test_output:
                progress("analyzing failure...")
                previous_failure_context = current_failure_context
                latest_failed_test_output = latest_test_output
                failed_test_runs += 1
                current_failure_count = extract_failure_count(latest_test_output)
                failure_type = classify_failure_type(latest_test_output)
                failure_context = extract_failure_context(latest_test_output, repo)
                failure_signature = normalize_failure_output(latest_test_output)
                current_attempt_files = sorted(pending_modified_files)
                current_attempt_diff = filtered_git_diff_output(repo, current_attempt_files)
                structural_ok, structural_output, structural_failure_type = validate_structural_safety(
                    repo,
                    current_attempt_files,
                )
                structural_breakage = not structural_ok and structural_failure_type in {
                    FAILURE_IMPORT_ERROR,
                    FAILURE_SYNTAX_ERROR,
                }
                if structural_breakage:
                    failure_type = structural_failure_type
                    failure_signature = normalize_failure_output(
                        latest_test_output + "\n" + structural_output
                    )
                attempt_score, attempt_score_reasons = score_attempt(
                    last_failure_signature,
                    failure_signature,
                    previous_failure_count,
                    current_failure_count,
                    previous_failure_type,
                    failure_type,
                    diff_reasoning,
                    structural_breakage,
                    len(current_attempt_files),
                    current_attempt_diff,
                )
                last_attempt_score = attempt_score
                if failure_signature and failure_signature == last_failure_signature:
                    repeated_failure_count += 1
                else:
                    repeated_failure_count = 1
                last_failure_signature = failure_signature
                last_failed_attempt_files = current_attempt_files
                pending_modified_files.clear()
                score_driven_decisions = []
                recent_scores.append(attempt_score)
                if len(recent_scores) > RECENT_SCORE_HISTORY:
                    recent_scores = recent_scores[-RECENT_SCORE_HISTORY:]

                failure_type_worsened = (
                    FAILURE_RANK.get(failure_type, 0) < FAILURE_RANK.get(previous_failure_type, 0)
                )

                if attempt_score <= -2:
                    material_regression = True
                    stronger_inspection_required = True
                    score_escalation_pressure = min(2, score_escalation_pressure + 1)
                    score_driven_decisions.append(
                        "Material regression detected: biasing toward rollback and faster strategy escalation."
                    )
                else:
                    material_regression = False

                if failure_type_worsened:
                    material_regression = True
                    stronger_inspection_required = True
                    score_escalation_pressure = min(2, score_escalation_pressure + 1)
                    score_driven_decisions.append(
                        "Failure type worsened: preferring rollback and faster escalation."
                    )

                if structural_breakage:
                    material_regression = True
                    stronger_inspection_required = True
                    score_escalation_pressure = max(score_escalation_pressure, 1)
                    score_driven_decisions.append(
                        "Structural breakage detected: treating this as a regression and preferring rollback to the best attempt."
                    )

                if attempt_score == 0:
                    zero_score_streak += 1
                    if zero_score_streak >= 2:
                        stronger_inspection_required = True
                        score_escalation_pressure = max(score_escalation_pressure, 1)
                        score_driven_decisions.append(
                            "Stagnation detected after two zero scores: escalating strategy one level and requiring stronger inspection."
                        )
                elif attempt_score >= 2:
                    zero_score_streak = 0
                    stronger_inspection_required = False
                    score_escalation_pressure = 0
                    score_driven_decisions.append(
                        "Positive score detected: resetting stagnation tracking and keeping the current base strategy."
                    )
                else:
                    zero_score_streak = 0

                if len(recent_scores) >= 2 and recent_scores[-1] < 0 and recent_scores[-2] < 0:
                    score_escalation_pressure = max(score_escalation_pressure, 1)
                    score_driven_decisions.append(
                        "Recent score history is negative twice in a row: escalating faster."
                    )

                if material_regression:
                    repeated_regression_count += 1
                else:
                    repeated_regression_count = 0

                if repeated_regression_count >= 2:
                    stronger_inspection_required = True
                    score_escalation_pressure = max(score_escalation_pressure, 1)
                    score_driven_decisions.append(
                        "Repeated regression detected over two attempts: triggering rollback."
                    )

                auto_rollback_required = (
                    attempt_score <= -2
                    or failure_type_worsened
                    or repeated_regression_count >= 2
                    or structural_breakage
                )

                require_diff_before_write = (
                    repeated_failure_count >= 2
                    or stronger_inspection_required
                    or material_regression
                )
                diff_seen_since_failure = not require_diff_before_write
                auto_restored_files = []
                read_test_files_since_failure.clear()
                read_target_files_since_failure.clear()
                diagnosis_explanation = ""
                diff_reasoning = ""
                last_diff_output = ""
                require_diff_reasoning = bool(last_failed_attempt_files)
                edit_plan = ""
                edit_scope = ""
                test_expectation = extract_test_expectation(latest_test_output)
                test_alignment = ""
                read_files_since_failure.clear()
                search_count_this_attempt = 0
                search_terms_this_attempt.clear()
                current_memory_hint = match_pattern_memory(
                    pattern_memory,
                    build_failure_signature(failure_type, failure_context, None),
                )
                if current_memory_hint.get("matched"):
                    print("Loaded prior pattern")
                    if current_memory_hint.get("successful_strategy"):
                        print(f"Reusing successful strategy: {current_memory_hint['successful_strategy']}")
                    if current_memory_hint.get("failed_strategies"):
                        print(
                            "Avoiding known failed strategy: "
                            + ", ".join(current_memory_hint.get("failed_strategies", [])[:3])
                        )
                relevant_context = extract_relevant_file_context(
                    repo,
                    latest_test_output,
                    last_failed_attempt_files,
                    sorted(search_hit_files),
                    current_memory_hint,
                )
                progress("searching relevant files...")
                search_hit_files.clear()
                selected_relevant_files = relevant_context["selected"]
                required_test_files = relevant_context["required_test_files"]
                required_impl_files = relevant_context["required_impl_files"]
                required_traceback_files = relevant_context["required_traceback_files"]
                primary_relevant_file = relevant_context["primary_file"]
                search_trigger_reasons, suggested_search_terms = evaluate_search_requirement(
                    latest_test_output,
                    relevant_context,
                    read_files_since_failure,
                    failed_test_runs,
                    attempt_score,
                    zero_score_streak,
                    material_regression,
                    failure_type_worsened,
                    current_memory_hint,
                )
                search_required_this_attempt = bool(search_trigger_reasons and suggested_search_terms)
                precision_patch = extract_precision_patch_context(
                    latest_test_output,
                    relevant_context,
                    failure_context,
                )
                if summarize_target_files(relevant_context):
                    progress(f"target files: {summarize_target_files(relevant_context)}")
                failure_signature_data = build_failure_signature(failure_type, failure_context, precision_patch)
                current_strategy_type = classify_attempt_strategy_type(
                    strategy_mode,
                    failure_type,
                    edit_scope,
                    current_attempt_diff,
                    precision_patch,
                )
                hypothesis_result = evaluate_hypothesis_result(
                    attempt_score,
                    previous_failure_type,
                    failure_type,
                )
                attempt_history.append(
                    {
                        "step": step,
                        "strategy_type": current_strategy_type,
                        "score": attempt_score,
                        "failure_type": failure_type,
                        "failure_context": failure_context,
                        "hypothesis_text": current_hypothesis.get("text", ""),
                        "related_symbols": current_hypothesis.get("symbols", []),
                        "related_files": current_hypothesis.get("files", []),
                        "hypothesis_result": hypothesis_result,
                        "plan_steps": current_plan.get("steps", [])[:],
                        "plan_files": current_plan.get("files", [])[:],
                        "plan_symbols": current_plan.get("symbols", [])[:],
                        "executed_plan_step": current_plan.get("current_step_index", 0) + 1,
                    }
                )
                run_metrics["attempts"].append(
                    {
                        "step": step,
                        "strategy_type": current_strategy_type,
                        "hypothesis_result": hypothesis_result,
                        "candidate_scores": [],
                        "candidate_count": 0,
                        "candidate_rejections": 0,
                        "validation_outcomes": [],
                    }
                )
                diversification = choose_diversified_strategy_guidance(attempt_history)
                if hypothesis_result == "rejected" and current_strategy_type and not diversification.get("reason"):
                    diversification = {
                        "preferred": "",
                        "avoid": current_strategy_type,
                        "reason": f"The previous hypothesis was rejected. Avoid repeating the same {current_strategy_type} reasoning path.",
                    }
                strategy_mode = determine_strategy_mode(
                    failed_test_runs,
                    repeated_failure_count,
                    failure_type,
                    score_escalation_pressure,
                )
                strategy_mode = apply_memory_strategy_hint(strategy_mode, current_memory_hint, diversification)
                if best_attempt is None or attempt_score > best_attempt["score"]:
                    best_attempt = {
                        "step": step,
                        "score": attempt_score,
                        "strategy_type": current_strategy_type,
                        "reasons": attempt_score_reasons[:],
                        "failure_type": failure_type,
                        "modified_files": last_failed_attempt_files[:],
                        "diff_text": current_attempt_diff,
                        "file_state_ref": "git_diff_snapshot" if current_attempt_diff else "",
                    }
                    print(
                        "Best attempt updated: "
                        f"step {best_attempt['step']} score {best_attempt['score']} "
                        f"ref={best_attempt['file_state_ref'] or 'none'}"
                    )
                print(f"Active strategy mode: {strategy_mode}")
                print(f"Detected failure type: {failure_type}")
                print(format_failure_context(failure_context))
                print(f"Chosen strategy type: {current_strategy_type}")
                print(f"Hypothesis evaluation: {hypothesis_result}")
                print(f"Per-test improvement status: {evaluate_test_progress(previous_failure_context, failure_context, False)}")
                if current_plan.get("steps"):
                    print(f"Executed plan step: {current_plan.get('current_step_index', 0) + 1}")
                print(f"Attempt score: {attempt_score}")
                print("Attempt score reasons:")
                for reason in attempt_score_reasons:
                    print(f"- {reason}")
                if structural_breakage:
                    print("\n=== STRUCTURAL VALIDATION ===")
                    print(
                        json.dumps(
                            {
                                "ok": structural_ok,
                                "failure_type": structural_failure_type,
                                "output": structural_output,
                            },
                            indent=2,
                        )
                    )
                if test_expectation:
                    print("\n=== TEST EXPECTATION ===")
                    print(test_expectation)
                if score_driven_decisions:
                    print("Score-driven decisions:")
                    for decision in score_driven_decisions:
                        print(f"- {decision}")
                if best_attempt:
                    print(
                        "Best attempt so far: "
                        f"step {best_attempt['step']} score {best_attempt['score']} "
                        f"failure_type={best_attempt['failure_type']} "
                        f"files={len(best_attempt['modified_files'])}"
                    )
                if selected_relevant_files:
                    print("\n=== RELEVANT FILES ===")
                    print(format_relevant_file_context(relevant_context))
                if search_required_this_attempt:
                    print("\n=== SEARCH GUIDANCE ===")
                    for reason in search_trigger_reasons:
                        print(f"- {reason}")
                    print("Suggested search terms:")
                    for term in suggested_search_terms:
                        print(f"- {term}")
                if precision_patch.get("active"):
                    print(
                        "Precision patch mode active: "
                        f"file={precision_patch['file']} symbol={precision_patch.get('symbol') or '(localized block)'}"
                    )
                if diversification.get("reason"):
                    print(f"Strategy switch reason: {diversification['reason']}")

                if auto_rollback_required and last_failed_attempt_files:
                    print("\n=== AUTO-ROLLBACK ===")
                    run_metrics["rollback_count"] += 1
                    rollback_to_best = (
                        best_attempt is not None
                        and best_attempt["score"] > attempt_score
                        and bool(best_attempt.get("diff_text"))
                    )
                    rollback_reason = []
                    if attempt_score <= -2:
                        rollback_reason.append("score <= -2")
                    if failure_type_worsened:
                        rollback_reason.append("failure type worsened")
                    if repeated_regression_count >= 2:
                        rollback_reason.append("repeated regression")
                    if structural_breakage:
                        rollback_reason.append("structural breakage")
                    print("Rollback trigger: " + ", ".join(rollback_reason))
                    rollback_paths = last_failed_attempt_files[:]
                    if rollback_to_best:
                        print(
                            "Using best attempt rollback: "
                            f"step {best_attempt['step']} score {best_attempt['score']}"
                        )
                        rollback_paths = sorted(
                            set(rollback_paths) | set(best_attempt.get("modified_files", []))
                        )

                    for restore_path in rollback_paths:
                        restore_result = handle_tool(
                            repo,
                            args.max_file_chars,
                            "git_restore_file",
                            json.dumps({"path": restore_path}),
                            latest_test_passed,
                        )
                        print(f"git_restore_file({json.dumps({'path': restore_path})})")
                        print("Tool result:")
                        print(restore_result)
                        auto_restored_files.append(restore_path)

                    if rollback_to_best:
                        applied, apply_output = apply_diff_snapshot(repo, best_attempt["diff_text"])
                        print("\n=== BEST ATTEMPT RESTORE ===")
                        print(
                            json.dumps(
                                {
                                    "ok": applied,
                                    "step": best_attempt["step"],
                                    "score": best_attempt["score"],
                                    "file_state_ref": best_attempt.get("file_state_ref", ""),
                                    "output": apply_output,
                                },
                                indent=2,
                            )
                        )

                    pending_modified_files.clear()

                attempt_notes.append(f"Step {step} test output:\n{latest_test_output[:1500]}")
                attempt_notes.append(format_failure_context(failure_context))
                if structural_breakage:
                    attempt_notes.append(
                        "Step "
                        f"{step} structural validation:\n"
                        f"- failure_type: {structural_failure_type}\n"
                        f"- output: {structural_output[:1500]}"
                    )
                if test_expectation:
                    attempt_notes.append(f"Step {step} test expectation:\n{test_expectation[:1500]}")
                attempt_notes.append(
                    f"Step {step} score: {attempt_score}\n" + "\n".join(f"- {reason}" for reason in attempt_score_reasons)
                )
                attempt_notes.append(
                    f"Step {step} strategy: {current_strategy_type}\n"
                    + (
                        f"- switch_reason: {diversification['reason']}"
                        if diversification.get("reason")
                        else "- switch_reason: none"
                    )
                )
                if current_hypothesis.get("text"):
                    attempt_notes.append(
                        f"Step {step} hypothesis:\n"
                        f"- text: {current_hypothesis['text']}\n"
                        f"- result: {hypothesis_result}\n"
                        + "\n".join(f"- symbol: {symbol}" for symbol in current_hypothesis.get("symbols", []))
                        + ("\n" if current_hypothesis.get("symbols") else "")
                        + "\n".join(f"- file: {path}" for path in current_hypothesis.get("files", []))
                    )
                if current_plan.get("steps"):
                    attempt_notes.append(
                        f"Step {step} plan:\n"
                        + "\n".join(f"{idx}. {plan_step}" for idx, plan_step in enumerate(current_plan["steps"], start=1))
                        + f"\n- executed_step: {current_plan.get('current_step_index', 0) + 1}"
                    )
                if current_memory_hint.get("matched"):
                    attempt_notes.append(
                        "Pattern memory:\n"
                        f"- successful_strategy: {current_memory_hint.get('successful_strategy', '')}\n"
                        + "\n".join(f"- successful_file: {path}" for path in current_memory_hint.get("successful_files", []))
                    )
                if score_driven_decisions:
                    attempt_notes.append(
                        f"Step {step} score decisions:\n" + "\n".join(f"- {decision}" for decision in score_driven_decisions)
                    )
                if best_attempt:
                    attempt_notes.append(
                        "Best attempt so far:\n"
                        f"- step: {best_attempt['step']}\n"
                        f"- score: {best_attempt['score']}\n"
                        f"- failure_type: {best_attempt['failure_type']}\n"
                        + "\n".join(f"- reason: {reason}" for reason in best_attempt["reasons"])
                    )
                if selected_relevant_files:
                    attempt_notes.append(format_relevant_file_context(relevant_context))
                if search_required_this_attempt:
                    attempt_notes.append(
                        "Search required before editing:\n"
                        + "\n".join(f"- reason: {reason}" for reason in search_trigger_reasons)
                        + "\n"
                        + "\n".join(f"- search_term: {term}" for term in suggested_search_terms)
                    )
                if precision_patch.get("active"):
                    attempt_notes.append(
                        "Precision patch mode:\n"
                        f"- file: {precision_patch['file']}\n"
                        f"- symbol: {precision_patch.get('symbol') or '(localized block)'}\n"
                        f"- reason: {precision_patch.get('reason', '')}"
                    )
                critique = get_critique("\n\n".join(attempt_notes[-4:]), latest_test_output)
                print("\n=== CRITIQUE ===")
                print(critique)
                previous_failure_type = failure_type
                previous_failure_count = current_failure_count

                if repeated_failure_count >= MAX_REPEATED_FAILURES:
                    blocked = detect_blocked_state(
                        failure_type,
                        latest_test_output,
                        relevant_context,
                        precommit_rejection_count,
                        repeated_failure_count,
                        zero_score_streak,
                        API_SAFETY_STATE["likely_rate_limit_hits"],
                    )
                    if blocked:
                        run_metrics["blocked_reason"] = blocked["reason"]
                        run_metrics["remote_blocked_kind"] = blocked.get("kind")
                        print(format_blocked_message(blocked))
                    print("\n=== STOPPING EARLY ===")
                    print(
                        f"Stopping after {repeated_failure_count} repeated failing test outputs. "
                        "Inspect the last diff and restore recent failed edits before trying again."
                    )
                    raise SystemExit(1)

                continue_plan = (
                    hypothesis_result == "confirmed"
                    and current_plan.get("steps")
                    and current_plan.get("current_step_index", 0) < len(current_plan["steps"]) - 1
                )
                next_plan = build_attempt_plan(
                    relevant_context,
                    precision_patch,
                    failure_context,
                    current_plan,
                    continue_plan,
                )
                next_hypothesis = build_attempt_hypothesis(
                    failure_type,
                    strategy_mode,
                    relevant_context,
                    precision_patch,
                    failure_context,
                    diversification,
                    next_plan,
                )
                progress(f"forming hypothesis...")
                progress(f"hypothesis: {next_hypothesis['text'][:160]}")

                messages.append(
                    {
                        "role": "user",
                        "content": build_recovery_prompt(
                            critique,
                            strategy_mode,
                            failure_type,
                            precision_patch,
                            diversification,
                            next_hypothesis,
                            next_plan,
                            current_memory_hint,
                            repeated_failure_count,
                            last_failed_attempt_files,
                            require_diff_before_write,
                            auto_restored_files,
                            (
                                "Repository search is required before editing.\n"
                                + "\n".join(f"- {reason}" for reason in search_trigger_reasons)
                                + (
                                    "\nSuggested search terms:\n"
                                    + "\n".join(f"- {term}" for term in suggested_search_terms)
                                    if suggested_search_terms
                                    else ""
                                )
                            )
                            if search_required_this_attempt
                            else "",
                        ),
                    }
                )
                repeated_rejection = (
                    hypothesis_result == "rejected"
                    and len(attempt_history) >= 2
                    and attempt_history[-2].get("strategy_type") == current_strategy_type
                    and attempt_history[-2].get("hypothesis_result") == "rejected"
                )
                pattern_memory = update_pattern_memory(
                    pattern_memory,
                    failure_signature_data,
                    current_strategy_type,
                    "rejected" if repeated_rejection else hypothesis_result,
                    current_hypothesis.get("files", []),
                    current_hypothesis.get("symbols", []),
                )
                save_pattern_memory(repo, pattern_memory)
                current_failure_context = failure_context
                current_plan = next_plan
                current_hypothesis = next_hypothesis
                run_metrics["total_attempts"] = max(run_metrics["total_attempts"], step)

            continue

        final_text = (msg.content or "").strip()
        if final_text:
            print("\n=== MODEL RESPONSE ===")
            print(final_text)

            explanation = extract_diagnosis_explanation(final_text)
            if strategy_mode == STRATEGY_TEST_FIRST_DIAGNOSIS and explanation and not diagnosis_explanation:
                diagnosis_explanation = explanation
                attempt_notes.append(f"Step {step} diagnosis:\n{diagnosis_explanation[:1500]}")
                print("\n=== DIAGNOSIS EXPLANATION ===")
                print(diagnosis_explanation)
                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Diagnosis recorded. Continue with tool calls, and keep the fix aligned with that explanation.",
                    }
                )
                continue

            reasoning = extract_diff_reasoning(final_text)
            if (
                require_diff_reasoning
                and last_diff_output
                and reasoning
                and not diff_reasoning
            ):
                diff_reasoning = reasoning
                attempt_notes.append(f"Step {step} diff reasoning:\n{diff_reasoning[:1500]}")
                print("\n=== DIFF REASONING ===")
                print(diff_reasoning)
                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Diff reasoning recorded. Continue with tool calls, and keep the next edit aligned with that review.",
                    }
                )
                continue

            plan = extract_edit_plan(final_text)
            if plan and not edit_plan:
                edit_plan = plan
                attempt_notes.append(f"Step {step} plan:\n{edit_plan[:1500]}")
                print("\n=== EDIT PLAN ===")
                print(edit_plan)
                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Plan recorded. Continue with tool calls and execute that plan carefully.",
                    }
                )
                continue

            scope = extract_edit_scope(final_text)
            if scope and not edit_scope:
                edit_scope = scope
                attempt_notes.append(f"Step {step} edit scope:\n{edit_scope[:1500]}")
                print("\n=== EDIT SCOPE ===")
                print(edit_scope)
                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Edit scope recorded. Keep the change minimal and confined to that target unless new evidence requires expanding it.",
                    }
                )
                continue

            alignment = extract_test_alignment(final_text)
            if test_expectation and alignment and not test_alignment:
                test_alignment = alignment
                attempt_notes.append(f"Step {step} test alignment:\n{test_alignment[:1500]}")
                print("\n=== TEST ALIGNMENT ===")
                print(test_alignment)
                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Test expectation alignment recorded. Continue with tool calls and keep the fix consistent with that expectation.",
                    }
                )
                continue

            pseudo = extract_pseudo_tool_call(final_text)
            if pseudo:
                tool_name, tool_args = pseudo
                print("\n=== SALVAGED TOOL CALL ===")
                print(f"{tool_name}({tool_args})")

                write_error = validate_write_request(
                    repo,
                    tool_name,
                    tool_args,
                    strategy_mode,
                    require_diff_before_write,
                    diff_seen_since_failure,
                    read_test_files_since_failure,
                    read_target_files_since_failure,
                    diagnosis_explanation,
                    diff_reasoning,
                    require_diff_reasoning,
                    edit_plan,
                    edit_scope,
                    test_expectation,
                    test_alignment,
                    required_test_files,
                    required_impl_files,
                    required_traceback_files,
                    read_files_since_failure,
                    search_required_this_attempt,
                    search_count_this_attempt,
                    search_trigger_reasons,
                    suggested_search_terms,
                    precision_patch,
                )
                if write_error is not None:
                    result = write_error
                elif tool_name == "search_repo":
                    try:
                        search_args = json.loads(tool_args) if tool_args else {}
                    except json.JSONDecodeError:
                        search_args = {}
                    term = (search_args.get("term") or "").strip()
                    if not term:
                        result = json.dumps({"ok": False, "error": "Search term is required."}, indent=2)
                    elif term in search_terms_this_attempt:
                        result = json.dumps(
                            {"ok": False, "error": f"Repeated search not allowed in this attempt: {term}"},
                            indent=2,
                        )
                    elif search_count_this_attempt >= MAX_SEARCHES_PER_ATTEMPT:
                        result = json.dumps({"ok": False, "error": "Search limit reached for this attempt."}, indent=2)
                    else:
                        reason_text = "; ".join(search_trigger_reasons) or "gathering additional repository context"
                        print(f"Search triggered: {reason_text}")
                        print(f"Search term: {term}")
                        search_terms_this_attempt.add(term)
                        search_count_this_attempt += 1
                        result = handle_tool(repo, args.max_file_chars, tool_name, tool_args, latest_test_passed)
                else:
                    result = handle_tool(repo, args.max_file_chars, tool_name, tool_args, latest_test_passed)
                print("Tool result:")
                print(result)

                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous message described a tool call in text. I executed it for you. Continue by using real tool calls from now on."
                    }
                )
                messages.append({"role": "tool", "tool_call_id": f"salvaged_{step}", "content": result})

                modified_path = should_track_modified_file(tool_name, result)
                if modified_path:
                    pending_modified_files.add(modified_path)
                if tool_name == "git_restore_file":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    restored_path = data.get("path")
                    if data.get("ok") is True and isinstance(restored_path, str):
                        pending_modified_files.discard(restored_path)
                if tool_name == "git_restore_all":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("ok") is True:
                        pending_modified_files.clear()
                if tool_name == "git_diff":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("ok") is True:
                        diff_seen_since_failure = True
                if tool_name == "read_file":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    read_path = data.get("path")
                    if data.get("ok") is True and isinstance(read_path, str):
                        read_files_since_failure.add(read_path)
                        if is_test_file_path(read_path):
                            read_test_files_since_failure.add(read_path)
                        else:
                            read_target_files_since_failure.add(read_path)
                if tool_name == "search_repo":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("ok") is True:
                        search_required_this_attempt = False
                        for path in data.get("files", []):
                            if isinstance(path, str):
                                search_hit_files.add(path)
                        if latest_failed_test_output:
                            relevant_context = extract_relevant_file_context(
                                repo,
                                latest_failed_test_output,
                                last_failed_attempt_files,
                                sorted(search_hit_files),
                            )
                            selected_relevant_files = relevant_context["selected"]
                            required_test_files = relevant_context["required_test_files"]
                            required_impl_files = relevant_context["required_impl_files"]
                            required_traceback_files = relevant_context["required_traceback_files"]
                            primary_relevant_file = relevant_context["primary_file"]
                            precision_patch = extract_precision_patch_context(
                                latest_failed_test_output,
                                relevant_context,
                            )
                            if selected_relevant_files:
                                print("\n=== RELEVANT FILES (REFRESHED FROM SEARCH) ===")
                                print(format_relevant_file_context(relevant_context))
                            if precision_patch.get("active"):
                                print(
                                    "Precision patch mode active: "
                                    f"file={precision_patch['file']} symbol={precision_patch.get('symbol') or '(localized block)'}"
                                )

                if tool_name == "run_shell":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {"ok": False, "output": result, "command": ""}

                    if data.get("command") == args.test_cmd and data.get("ok") is True:
                        latest_test_passed = True
                        finalization = finalize_success(
                            repo,
                            args.max_file_chars,
                            messages,
                            latest_test_passed,
                            step,
                            args.test_cmd,
                            current_failure_context,
                            primary_relevant_file,
                            best_attempt,
                            current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                            args.dry_run,
                            args.show_diff,
                            resolved_mode,
                            publish_requested,
                            args.publish_message or "",
                        )
                        if finalization.get("rejected"):
                            precommit_rejection_count += 1
                            last_attempt_score = -3
                            recent_scores.append(-3)
                            if len(recent_scores) > RECENT_SCORE_HISTORY:
                                recent_scores = recent_scores[-RECENT_SCORE_HISTORY:]
                            run_metrics["attempts"].append(
                                {
                                    "step": step,
                                    "strategy_type": current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                                    "hypothesis_result": "rejected",
                                    "candidate_scores": [item.get("candidate_score", 0) for item in finalization.get("candidate_results", [])],
                                    "candidate_count": len(finalization.get("candidate_results", [])),
                                    "candidate_rejections": sum(1 for item in finalization.get("candidate_results", []) if not item.get("ok")),
                                    "validation_outcomes": [item.get("status", "") for item in finalization.get("candidate_results", [])],
                                }
                            )
                            for candidate_result in finalization.get("candidate_results", []):
                                if not candidate_result.get("ok"):
                                    pattern_memory = update_pattern_memory(
                                        pattern_memory,
                                        build_failure_signature(failure_type, current_failure_context, precision_patch),
                                        candidate_result.get("strategy_type", ""),
                                        "rejected",
                                        current_hypothesis.get("files", []),
                                        current_hypothesis.get("symbols", []),
                                    )
                            if precommit_rejection_count >= 2:
                                blocked = detect_blocked_state(
                                    failure_type,
                                    str(finalization.get("output", "")),
                                    {"selected": [{"score": 10}]},
                                    precommit_rejection_count,
                                    repeated_failure_count,
                                    zero_score_streak,
                                    API_SAFETY_STATE["likely_rate_limit_hits"],
                                )
                                if blocked:
                                    run_metrics["blocked_reason"] = blocked["reason"]
                                    run_metrics["remote_blocked_kind"] = blocked.get("kind")
                                    print(format_blocked_message(blocked))
                            save_pattern_memory(repo, pattern_memory)
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Pre-commit validation failed and the patch was rejected. "
                                        "Treat this as a failed attempt with a strong negative signal, "
                                        "change strategy, and do not try to commit until validation passes."
                                    ),
                                }
                            )
                            latest_test_passed = False
                            continue
                        chosen_candidate_name = finalization.get("chosen_candidate", "")
                        if chosen_candidate_name:
                            print(f"chosen candidate {chosen_candidate_name}")
                        run_metrics["attempts"].append(
                            {
                                "step": step,
                                "strategy_type": current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                                "hypothesis_result": "confirmed",
                                "candidate_scores": [item.get("candidate_score", 0) for item in finalization.get("candidate_results", [])],
                                "candidate_count": len(finalization.get("candidate_results", [])),
                                "candidate_rejections": sum(1 for item in finalization.get("candidate_results", []) if not item.get("ok")),
                                "validation_outcomes": [item.get("status", "") for item in finalization.get("candidate_results", [])],
                            }
                        )
                        pattern_memory = update_pattern_memory(
                            pattern_memory,
                            build_failure_signature(failure_type, current_failure_context, precision_patch),
                            current_strategy_type if 'current_strategy_type' in locals() else "fallback/default",
                            "confirmed",
                            current_hypothesis.get("files", []),
                            current_hypothesis.get("symbols", []),
                        )
                        save_pattern_memory(repo, pattern_memory)
                        run_metrics["success"] = True
                        run_metrics["attempts_to_success"] = step
                        run_metrics["total_attempts"] = max(run_metrics["total_attempts"], step)
                        run_metrics["likely_rate_limit_hits"] = API_SAFETY_STATE["likely_rate_limit_hits"]
                        run_metrics["cooldowns_triggered"] = API_SAFETY_STATE["cooldowns_triggered"]
                        summary_text = summarize_run_metrics(run_metrics)
                        print(summary_text)
                        append_run_metrics(repo, run_metrics)
                        comparison_text = analyze_run_comparison(load_recent_run_metrics(repo))
                        outcome_label = "dry-run" if args.dry_run else "success"
                        action_text, rerun_cmd, continue_cmd, full_suite_cmd = build_action_summary(repo, args.test_cmd, resolved_mode, outcome_label, args.dry_run)
                        write_run_artifacts(repo, artifact_dir, run_metrics, summary_text, comparison_text, rerun_cmd, continue_cmd, full_suite_cmd)
                        update_recent_state(
                            repo,
                            args.test_cmd,
                            resolved_mode,
                            "success",
                            artifact_dir,
                            target,
                            files_changed=finalization.get("changed_paths", []),
                            confidence=str(finalization.get("confidence_level", "") or ""),
                            blocked_reason=str(run_metrics.get("blocked_reason") or ""),
                        )
                        print(comparison_text)
                        print(action_text)
                        print(format_run_artifact_summary(repo, recent_state_path, config_path, artifact_dir, target))
                        if args.no_finalize:
                            fail_incomplete_without_finalization()
                        publish_summary = run_post_success_publish(
                            repo,
                            args.test_cmd,
                            step,
                            finalization.get("confidence_level", ""),
                            artifact_dir,
                            finalization.get("changed_paths", []),
                            args.publish_branch or "",
                            args.publish_pr,
                            args.publish_merge,
                            args.publish_merge_local_main,
                            args.publish_message or "",
                            target,
                            run_metrics.get("blocked_reason"),
                            publish_baseline_paths,
                            args.dry_run,
                            "validated-run",
                            True,
                            publish_requested,
                            auto_stage_safe_paths=not bool(args.no_auto_stage),
                            auto_remediate_blockers=not bool(args.no_auto_remediate_blockers),
                            explain_staging=bool(args.explain_staging),
                        )
                        print_post_success_publish_summary(publish_summary)
                        if publish_summary.get("publish_result_detail"):
                            print_publish_summary(publish_summary["publish_result_detail"])
                        print(format_final_operator_summary(publish_summary))
                        if publish_summary_requires_failure(publish_summary):
                            print("\nFailed: validation succeeded but publish did not complete successfully.", file=sys.stderr)
                            raise SystemExit(1)
                        return

                continue

            messages.append({"role": "assistant", "content": final_text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"You must use actual tool calls, not described JSON in text. "
                        f"If you think it is fixed, run `{args.test_cmd}`. "
                        "If you want a diff, call git_diff as a real tool."
                    ),
                }
            )
        else:
            messages.append({"role": "assistant", "content": ""})

    run_metrics["total_attempts"] = max(run_metrics["total_attempts"], args.max_steps)
    run_metrics["likely_rate_limit_hits"] = API_SAFETY_STATE["likely_rate_limit_hits"]
    run_metrics["cooldowns_triggered"] = API_SAFETY_STATE["cooldowns_triggered"]
    blocked = detect_blocked_state(
        failure_type,
        latest_failed_test_output,
        {"selected": selected_relevant_files},
        precommit_rejection_count,
        repeated_failure_count,
        zero_score_streak,
        API_SAFETY_STATE["likely_rate_limit_hits"],
    )
    if blocked:
        run_metrics["blocked_reason"] = blocked["reason"]
        run_metrics["remote_blocked_kind"] = blocked.get("kind")
        print(format_blocked_message(blocked))
    summary_text = summarize_run_metrics(run_metrics)
    print(summary_text)
    append_run_metrics(repo, run_metrics)
    comparison_text = analyze_run_comparison(load_recent_run_metrics(repo))
    action_text, rerun_cmd, continue_cmd, full_suite_cmd = build_action_summary(repo, args.test_cmd, resolved_mode, "failure", args.dry_run)
    write_run_artifacts(repo, artifact_dir, run_metrics, summary_text, comparison_text, rerun_cmd, continue_cmd, full_suite_cmd)
    update_recent_state(
        repo,
        args.test_cmd,
        resolved_mode,
        "blocked" if blocked else "failed",
        artifact_dir,
        target,
        blocked_reason=str(run_metrics.get("blocked_reason") or ""),
    )
    print(comparison_text)
    print(action_text)
    print(format_run_artifact_summary(repo, recent_state_path, config_path, artifact_dir, target))
    print_post_success_publish_summary(
        {
            "validation_state": "failed",
            "validation_result": "failed",
            "validation_commit_match": bool(parse_head_commit(repo) if is_git_repo(repo) else ""),
            "last_validated_commit": parse_head_commit(repo) if is_git_repo(repo) else "",
            "current_commit": parse_head_commit(repo) if is_git_repo(repo) else "",
            "validation_age_seconds": 0 if is_git_repo(repo) else -1,
            "publish_requested": publish_requested,
            "publish_triggered": False,
            "publish_mode": "validated-run",
            "publish_result": "not_requested" if not publish_requested else "not_requested",
            "publish_reason": ("blocked_by_validation" if publish_requested else ""),
            "publish_detail_reason": (f"publish blocked because validation_result=failed; use --force-publish to override" if publish_requested else ""),
            "pr_created_or_reused": False,
            "pr_merged": False,
            "local_main_synced": False,
        }
    )
    print(
        format_final_operator_summary(
            {
                "validation_state": "failed",
                "validation_result": "failed",
                "validation_commit_match": bool(parse_head_commit(repo) if is_git_repo(repo) else ""),
                "last_validated_commit": parse_head_commit(repo) if is_git_repo(repo) else "",
                "current_commit": parse_head_commit(repo) if is_git_repo(repo) else "",
                "validation_age_seconds": 0 if is_git_repo(repo) else -1,
                "publish_requested": publish_requested,
                "publish_triggered": False,
                "publish_mode": "validated-run",
                "publish_result": "not_requested",
                "publish_reason": ("blocked_by_validation" if publish_requested else ""),
                "publish_detail_reason": (f"publish blocked because validation_result=failed; use --force-publish to override" if publish_requested else ""),
                "pr_created_or_reused": False,
                "pr_merged": False,
                "local_main_synced": False,
            }
        )
    )
    print("\nFailed: reached max steps without confirmed passing tests.", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
