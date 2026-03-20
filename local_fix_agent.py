from openai import OpenAI
from pathlib import Path, PurePosixPath
import ast
import atexit
import argparse
import json
import os
import re
import secrets
import shutil
import shlex
import subprocess
import sys
import tempfile
import time

MODEL = "qwen2.5-coder:14b"
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
METRICS_FILE_NAME = ".fix_agent_metrics.json"
CONFIG_FILE_NAME = ".fix_agent_config.json"
RECENT_STATE_FILE_NAME = ".fix_agent_recent.json"
PUBLISH_STATE_FILE_NAME = ".ai_publish_state.json"
RUN_ARTIFACTS_DIR_NAME = ".fix_agent_runs"
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
    for key in ["HTTP_PROXY", "HTTPS_PROXY"]:
        value = settings.get(key, "")
        if value:
            CURRENT_SUBPROCESS_ENV[key] = value
            CURRENT_SUBPROCESS_ENV[key.lower()] = value
    API_SAFETY_STATE["proxy_enabled"] = bool(settings.get("HTTP_PROXY") or settings.get("HTTPS_PROXY"))
    API_SAFETY_STATE["http_proxy"] = settings.get("HTTP_PROXY", "")
    API_SAFETY_STATE["https_proxy"] = settings.get("HTTPS_PROXY", "")
    API_SAFETY_STATE["run_budget"] = int(settings.get("run_budget", 0) or 0)
    API_SAFETY_STATE["attempt_budget"] = int(settings.get("attempt_budget", 0) or 0)
    API_SAFETY_STATE["likely_rate_limit_hits"] = 0
    API_SAFETY_STATE["attempt_rate_limit_hits"] = 0
    API_SAFETY_STATE["cooldowns_triggered"] = 0
    API_SAFETY_STATE["last_rate_limit_output"] = ""


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
    code, _ = run_subprocess(["git", "rev-parse", "--is-inside-work-tree"], repo)
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
    body = line[3:] if len(line) > 3 else ""
    if " -> " in body:
        return body.split(" -> ", 1)[1].strip()
    return body.strip()


def is_ignored_change_path(path: str) -> bool:
    if not path:
        return True
    rel = Path(path)
    path_str = str(rel)
    return (
        path_str == MEMORY_FILE_NAME
        or path_str == PUBLISH_STATE_FILE_NAME
        or
        path_str.endswith(".bak")
        or path_str.endswith(".pyc")
        or "__pycache__/" in path_str
        or any(part in IGNORE_DIRS for part in rel.parts)
    )


def filter_status_lines(output: str, ignore_all_ignored_dirs: bool = False) -> list[str]:
    filtered = []
    for line in output.splitlines():
        path = extract_status_path(line)
        if is_ignored_change_path(path):
            continue
        filtered.append(line)
    return filtered


def filtered_git_status_output(repo: Path, ignore_all_ignored_dirs: bool = False) -> str:
    code, output = run_subprocess(["git", "status", "--short", "--untracked-files=all"], repo)
    if code != 0:
        return output
    return "\n".join(filter_status_lines(output, ignore_all_ignored_dirs)).strip()


def meaningful_changed_paths(repo: Path) -> list[str]:
    status_output = filtered_git_status_output(repo, ignore_all_ignored_dirs=True)
    paths = []
    seen = set()
    for line in status_output.splitlines():
        path = extract_status_path(line)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def classify_git_working_tree(repo: Path) -> dict:
    status_output = filtered_git_status_output(repo, ignore_all_ignored_dirs=True)
    has_unstaged = False
    has_staged = False
    has_untracked = False
    for line in status_output.splitlines():
        if line.startswith("??"):
            has_untracked = True
            continue
        staged_flag = line[0] if len(line) > 0 else " "
        unstaged_flag = line[1] if len(line) > 1 else " "
        if staged_flag not in {" ", "?"}:
            has_staged = True
        if unstaged_flag != " ":
            has_unstaged = True
    return {
        "status_output": status_output,
        "clean": not status_output.strip(),
        "has_unstaged": has_unstaged,
        "has_staged": has_staged,
        "has_untracked": has_untracked,
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


def load_recent_state() -> dict:
    return load_json_file(script_state_path(RECENT_STATE_FILE_NAME), {"recent_runs": []})


def save_recent_state(state: dict) -> None:
    path = script_state_path(RECENT_STATE_FILE_NAME)
    try:
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def update_recent_state(repo: Path, test_cmd: str, mode: str, success: bool, artifact_dir: Path | None = None, target: str = "") -> Path:
    state = load_recent_state()
    runs = [item for item in state.get("recent_runs", []) if isinstance(item, dict)]
    runs.append(
        {
            "repo": str(repo),
            "test_cmd": test_cmd,
            "mode": mode,
            "success": success,
            "artifact_dir": str(artifact_dir) if artifact_dir else "",
            "target": target,
            "ts": int(time.time()),
        }
    )
    state["recent_runs"] = runs[-10:]
    save_recent_state(state)
    return script_state_path(RECENT_STATE_FILE_NAME)


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
    script_rel = CURRENT_VALIDATION_PLAN.get("script_rel_path", "")
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
    return "\n".join(lines)


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
    cmd = "AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish"
    if include_pr:
        cmd += " --publish-pr"
    return cmd


def recommended_publish_current_command(include_pr: bool = True) -> str:
    cmd = "AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only"
    if include_pr:
        cmd += " --publish-pr"
    return cmd


def make_publish_result() -> dict:
    return {
        "published": False,
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
        },
        "summary_status": "",
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


def set_publish_outcome(result: dict, status: str, reason: str, next_action: str = "") -> dict:
    result["noop"] = status == "noop"
    result["reason"] = reason
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
    publish_result = str(summary.get("publish_result") or "not_requested").strip()
    return publish_result not in {"success", "noop", "not_requested"}


def resolve_publish_requested(args: argparse.Namespace) -> bool:
    if getattr(args, "publish_only", False):
        return True
    if getattr(args, "no_publish_on_success", False):
        return False
    return True


def format_bool(value: object) -> str:
    return "true" if bool(value) else "false"


def format_final_operator_summary(summary: dict) -> str:
    validation_result = str(summary.get("validation_result") or "failed").strip()
    publish_result = str(summary.get("publish_result") or "not_requested").strip()
    publish_requested = bool(summary.get("publish_requested"))
    if validation_result == "success":
        if publish_result == "success":
            return "FINAL: validation succeeded, publish succeeded"
        if publish_result == "noop":
            reason = str(summary.get("publish_reason") or "").strip().lower()
            if "already published" in reason or "already up to date" in reason or "no changes to publish" in reason:
                return "FINAL: publish noop (already up to date)"
            return "FINAL: validation succeeded, publish noop"
        if publish_requested:
            return f"FINAL: validation succeeded, publish {publish_result}"
        return "FINAL: validation succeeded"
    if validation_result == "blocked":
        if publish_result in {"success", "failed", "noop", "blocked", "failed_verification"}:
            return f"FINAL: validation blocked, publish {publish_result}"
        return "FINAL: validation blocked"
    return "FINAL: validation failed"


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
    try:
        data = json.loads(output or "[]")
    except json.JSONDecodeError:
        return ""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return str(first.get("url") or "").strip()
    return ""


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
    code, output = run_subprocess(["git", "pull", "--ff-only", "origin", "main"], repo)
    if switched:
        restore_code, restore_output = run_subprocess(["git", "checkout", current_branch], repo)
        if restore_code != 0:
            return False, f"Local main sync restored main, but could not switch back to {current_branch}: {restore_output}"
    if code != 0:
        return False, f"Local main sync failed: {output}"
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
    code, output = run_subprocess(["git", "rev-parse", "HEAD"], repo)
    return output.strip() if code == 0 else ""


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
) -> dict:
    result = make_publish_result()
    result["publish_scope"] = "current_repo_state" if publish_current_mode else "validated_run"
    result["requested"] = True
    publish_state = load_publish_state(repo)
    result["publish_state"] = publish_state
    result["state_loaded"] = bool(publish_state.get("timestamp"))
    result["transport_locked"] = bool(publish_state.get("ssh_confirmed"))
    result["environment"] = detect_publish_environment()
    result["recommended_command"] = recommended_publish_command(include_pr=publish_pr or publish_merge)

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
            "last_target_repo": result.get("target", {}).get("repo", ""),
            "last_control_path": result.get("control_path", ""),
        }
        save_publish_state(repo, state_payload)
        return result

    if target:
        result["remote_url"] = ""
        result["control_path"] = "blocked_missing_origin"
        return finish("blocked", "Publish requested, but this workflow only publishes local repos from the controlling machine. Remote publish is not handled.")
    if dry_run_mode:
        return finish("blocked", "Publish blocked because --dry-run skips commit creation and push.")
    if blocked_reason:
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

    current_paths = meaningful_changed_paths(repo)
    working_tree = classify_git_working_tree(repo)
    result["working_tree"] = working_tree
    if not publish_current_mode:
        unrelated_paths = sorted(set(current_paths) - set(changed_paths))
        if unrelated_paths:
            return finish("blocked", "Publish blocked because the working tree contains unrelated changes: " + ", ".join(unrelated_paths[:10]))
    head_sha = parse_head_commit(repo)
    result["fingerprint"] = {
        "matched_previous_success": False,
        "reason": "",
        "commit": head_sha,
        "branch": branch_to_push,
        "target_repo": result["target"].get("repo", ""),
    }
    if (
        publish_state.get("last_success")
        and publish_state.get("last_branch") == branch_to_push
        and publish_state.get("last_commit")
        and publish_state.get("last_commit") == head_sha
        and publish_state.get("last_target_repo") == result["target"].get("repo", "")
    ):
        result["control_path"] = "noop"
        result["fingerprint"]["matched_previous_success"] = True
        result["fingerprint"]["reason"] = "matched previous successful publish fingerprint"
        result["summary_status"] = "already published in previous run"
        return finish("noop", "Publish noop: already published in previous run.")
    already_pushed, local_head = branch_already_up_to_date(repo, branch_to_push, result["target"]["url"] or "origin")
    if publish_current_mode and working_tree["clean"]:
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

    if publish_current_mode:
        code, output = run_subprocess(["git", "add", "-A"], repo)
        if code != 0:
            return finish("blocked", f"Publish blocked because staging failed: {output}")
        result["summary_status"] = "staged current repo state"

        code, _ = run_subprocess(["git", "diff", "--cached", "--quiet"], repo)
        if code == 0:
            commit_ref = local_head or parse_head_commit(repo)
            mark_publish_noop(result, "no changes to publish", branch_to_push, result["remote_url"], commit_ref)
            return finish("noop")
        if code not in {0, 1}:
            return finish("blocked", "Publish blocked because staged-change detection failed.")
    else:
        code, output = run_subprocess(["git", "add", "-A", "--", *changed_paths], repo)
        if code != 0:
            return finish("blocked", f"Publish blocked because staging failed: {output}")

        status_after_add = filtered_git_status_output(repo)
        staged_paths = meaningful_changed_paths(repo)
        unrelated_staged = sorted(set(staged_paths) - set(changed_paths))
        if unrelated_staged:
            return finish("blocked", "Publish blocked because staging picked up unrelated files: " + ", ".join(unrelated_staged[:10]))
        if not status_after_add.strip():
            commit_ref = local_head or parse_head_commit(repo)
            if already_pushed:
                mark_publish_noop(result, "Publish noop: branch already up to date on origin.", branch_to_push, result["remote_url"], commit_ref)
                return finish("noop")
            mark_publish_noop(result, "Publish noop: nothing to commit.", branch_to_push, result["remote_url"], commit_ref)
            return finish("noop")

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
    set_publish_final(result, "failed", branch=branch_to_push, commit=commit_sha, remote=result["remote_url"], pr_url=None)

    if result["environment"].get("interactive"):
        if not prompt_yes_no(
            f"Push branch '{branch_to_push}' to origin?",
            default=True,
        ):
            return finish("blocked", "Publish stopped after commit because push was not confirmed.")

    code, output = run_subprocess(["git", "push", "-u", "origin", branch_to_push], repo)
    if code != 0:
        lowered = (output or "").lower()
        if any(token in lowered for token in ["authentication failed", "permission denied", "403", "could not read username"]):
            return finish("failed_auth", f"Publish blocked because push failed with an authentication error: {output}", "Verify GitHub auth for the resolved target and retry.")
        if "repository not found" in lowered and result["target"]["type"] == "fork":
            return finish("missing_fork", f"Publish blocked because the resolved fork target was not found: {output}", f"Create or verify fork `{result['target']['repo']}`, then set origin with `git remote set-url origin {result['target']['url']}` and retry.")
        return finish("failed", f"Publish blocked because push failed: {output}", "Inspect the resolved target URL, auth, and branch permissions before retrying.")
    result["attempted"] = True
    result["verification"] = verify_publish_sync(repo, branch_to_push, "origin")

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
                    pr_url = pr_output.strip().splitlines()[-1] if pr_output.strip() else ""
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
) -> dict:
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
    )
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
) -> dict:
    validation_result = "success" if validation_succeeded else ("blocked" if blocked_reason else "failed")
    summary = {
        "validation_result": validation_result,
        "publish_requested": False,
        "publish_triggered": False,
        "publish_mode": publish_mode,
        "pr_created_or_reused": False,
        "pr_merged": False,
        "local_main_synced": False,
        "publish_result": "not_requested",
        "publish_result_detail": None,
        "publish_reason": "",
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
        )
        summary["publish_triggered"] = True
        summary["publish_result_detail"] = publish_result
    elif validation_succeeded and publish_requested:
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
        )
        summary["publish_triggered"] = True
        summary["publish_result_detail"] = publish_result
    elif publish_requested and not validation_succeeded:
        summary["publish_requested"] = True
        summary["publish_result"] = "not_requested"
        summary["publish_reason"] = f"publish not run because validation_result={validation_result}"
    if summary["publish_requested"] and validation_succeeded and not summary["publish_triggered"]:
        summary["publish_result"] = "failed"
        summary["publish_reason"] = "publish requested after validation success, but publish flow did not run"
    elif summary.get("publish_result") != "not_requested" or summary.get("publish_result_detail"):
        summary["publish_result"] = summarize_publish_result(summary)
    publish_result = summary.get("publish_result_detail") or {}
    if not summary["publish_reason"]:
        summary["publish_reason"] = (
            publish_result.get("reason")
            or (publish_result.get("verification") or {}).get("reason")
            or publish_result.get("pr_reason")
            or ""
        )
    summary["pr_created_or_reused"] = bool(publish_result.get("pr_created_or_reused") or publish_result.get("pr_already_exists"))
    summary["pr_merged"] = bool(publish_result.get("pr_merged"))
    summary["local_main_synced"] = bool(publish_result.get("local_main_synced"))
    return summary


def print_post_success_publish_summary(summary: dict) -> None:
    print("\n=== VALIDATION RESULT ===")
    print(f"validation_result: {summary.get('validation_result', 'failed')}")
    print("\n=== POST-SUCCESS PUBLISH ===")
    print(f"publish_requested: {format_bool(summary.get('publish_requested'))}")
    print(f"publish_triggered: {format_bool(summary.get('publish_triggered'))}")
    print(f"publish_mode: {summary.get('publish_mode') or 'validated-run'}")
    if summary.get("publish_reason"):
        print(f"publish_reason: {summary['publish_reason']}")
    if not summary.get("publish_result_detail"):
        print("\n=== PUBLISH RESULT ===")
        print(f"publish_result: {summary.get('publish_result', 'not_requested')}")
    print(f"pr_created_or_reused: {format_bool(summary.get('pr_created_or_reused'))}")
    print(f"pr_merged: {format_bool(summary.get('pr_merged'))}")
    print(f"local_main_synced: {format_bool(summary.get('local_main_synced'))}")


def print_publish_summary(publish_result: dict) -> None:
    preflight = publish_result.get("preflight") or {}
    final = publish_result.get("final") or {}
    target = publish_result.get("target") or {}
    environment = publish_result.get("environment") or {}
    fingerprint = publish_result.get("fingerprint") or {}
    actions = publish_result.get("actions") or []
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
    if publish_result.get("publish_scope") == "current_repo_state":
        working_tree = publish_result.get("working_tree") or {}
        print(
            "working_tree: "
            f"clean={bool(working_tree.get('clean'))} "
            f"unstaged={bool(working_tree.get('has_unstaged'))} "
            f"staged={bool(working_tree.get('has_staged'))} "
            f"untracked={bool(working_tree.get('has_untracked'))}"
        )
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
    print(f"pr_url: {publish_result.get('pr_url') or '(none)'}")
    verification = publish_result.get("verification") or {}
    print(f"current_branch: {verification.get('current_branch') or '(none)'}")
    print(f"upstream_branch: {verification.get('upstream_branch') or '(none)'}")
    print(f"upstream_exists: {bool(verification.get('upstream_exists'))}")
    print(f"local_head: {verification.get('local_head') or '(none)'}")
    print(f"remote_head: {verification.get('remote_head') or '(none)'}")
    print(f"sync_verified: {bool(verification.get('synced'))}")
    print(f"pr_requested: {bool(publish_result.get('pr_requested'))}")
    print(f"pr_status: {publish_result.get('pr_status') or 'not_requested'}")
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

    if changed_paths and len(changed_paths) <= 2 and diff_size <= 2000 and all(item.get("ok") for item in candidate_results):
        level = "HIGH"
    elif diff_size > 5000 or len(changed_paths) > 3:
        level = "LOW"
    else:
        level = "MEDIUM"
    if CURRENT_VALIDATION_PLAN.get("active") and CURRENT_VALIDATION_PLAN.get("limited_validation") and level == "HIGH":
        level = "MEDIUM"

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
    return (
        f"Repository: {repo}\n"
        f"Current branch: {branch_name or '(unknown or no git branch)'}\n"
        f"Goal: make this pass: {test_cmd}\n"
        f"{validation_note}"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="Path to repo")
    parser.add_argument("--script", help="Path to a Python script; validation commands are discovered automatically.")
    parser.add_argument("--target", help="SSH host for remote execution.")
    parser.add_argument("--test-cmd", help="Test command")
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
    parser.add_argument("--publish-only", action="store_true", help="Publish the current repo state without running the repair loop or requiring a failing test command.")
    parser.add_argument("--publish-branch", help="Branch name to use for publish mode.")
    parser.add_argument("--publish-pr", action="store_true", help="After publish, attempt to open a pull request with GitHub CLI.")
    parser.add_argument("--publish-merge", action="store_true", help="After publish, auto-merge only when the PR is a safe self-owned fork PR.")
    parser.add_argument("--publish-merge-local-main", action="store_true", help="After a successful safe auto-merge, sync local main with origin/main.")
    parser.add_argument("--publish-message", help="Commit message to use for publish mode.")
    parser.add_argument("--http-proxy", help="HTTP proxy for subprocess-driven tasks.")
    parser.add_argument("--https-proxy", help="HTTPS proxy for subprocess-driven tasks.")
    parser.add_argument("--api-budget-run", type=int, help="Optional likely API-failure budget for the full run.")
    parser.add_argument("--api-budget-attempt", type=int, help="Optional likely API-failure budget per attempt.")
    parser.add_argument("--config", help="Path to config JSON file.")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-file-chars", type=int)
    args = parser.parse_args()

    repo, args.test_cmd, args.max_steps, args.max_file_chars, resolved_mode, mode_source, config_path, recent_state_path, safety_settings, target = resolve_run_settings(
        args,
        require_test_cmd=not args.publish_only,
    )
    if not target and not repo.exists():
        print(f"Missing repo path: {repo}", file=sys.stderr)
        raise SystemExit(1)

    configure_execution_target(target, repo)
    configure_subprocess_safety(safety_settings)
    publish_requested = resolve_publish_requested(args)
    if args.publish_only:
        startup_signal("Mode: publish current repo state")
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
    else:
        startup_signal("Starting repair loop...")
    print(f"Execution: {'remote' if target else 'local'}")
    if target:
        print(f"🌐 Target: {target}")
    print(f"Repository: {repo}")
    if CURRENT_VALIDATION_PLAN.get("active"):
        print(format_validation_plan_summary(CURRENT_VALIDATION_PLAN))
    if args.publish_only:
        print("Active mode: publish current repo state")
    elif publish_requested:
        print("Active mode: publish validated run")
    if args.explain_only:
        print("Explain-only mode: resolved settings only, no repair attempt will run.")
        print(format_run_artifact_summary(repo, recent_state_path, config_path))
        return
    if args.publish_only:
        publish_result = publish_current_repo_state(
            repo,
            args.publish_branch or "",
            args.publish_pr,
            args.publish_merge,
            args.publish_merge_local_main,
            args.publish_message or "",
            target,
            args.dry_run,
        )
        print_post_success_publish_summary(
            {
                "validation_result": "blocked",
                "publish_requested": True,
                "publish_triggered": True,
                "publish_mode": "current-repo-state",
                "publish_result": (publish_result.get("final") or {}).get("status") or "failed",
                "publish_result_detail": publish_result,
                "publish_reason": publish_result.get("reason") or (publish_result.get("verification") or {}).get("reason") or "",
                "pr_created_or_reused": bool(publish_result.get("pr_created_or_reused") or publish_result.get("pr_already_exists")),
                "pr_merged": bool(publish_result.get("pr_merged")),
                "local_main_synced": bool(publish_result.get("local_main_synced")),
            }
        )
        print_publish_summary(publish_result)
        print(format_final_operator_summary(
            {
                "validation_result": "blocked",
                "publish_requested": True,
                "publish_triggered": True,
                "publish_mode": "current-repo-state",
                "publish_result": (publish_result.get("final") or {}).get("status") or "failed",
                "publish_result_detail": publish_result,
                "publish_reason": publish_result.get("reason") or (publish_result.get("verification") or {}).get("reason") or "",
                "pr_created_or_reused": bool(publish_result.get("pr_created_or_reused") or publish_result.get("pr_already_exists")),
                "pr_merged": bool(publish_result.get("pr_merged")),
                "local_main_synced": bool(publish_result.get("local_main_synced")),
            }
        ))
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
    publish_baseline_paths = meaningful_changed_paths(repo) if args.publish else []

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(STRATEGY_MINIMAL_PATCH, FAILURE_UNKNOWN, None, None, None, None, None),
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
        )
        print(f"Active strategy mode: {strategy_mode}")
        print(f"Detected failure type: {failure_type}")
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
                    args.publish,
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
                update_recent_state(repo, args.test_cmd, resolved_mode, True, artifact_dir, target)
                print(comparison_text)
                print(action_text)
                print(format_run_artifact_summary(repo, recent_state_path, config_path, artifact_dir, target))
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
                )
                print_post_success_publish_summary(publish_summary)
                if publish_summary.get("publish_result_detail"):
                    print_publish_summary(publish_summary["publish_result_detail"])
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
                            args.publish,
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
                        update_recent_state(repo, args.test_cmd, resolved_mode, True, artifact_dir, target)
                        print(comparison_text)
                        print(action_text)
                        print(format_run_artifact_summary(repo, recent_state_path, config_path, artifact_dir, target))
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
    update_recent_state(repo, args.test_cmd, resolved_mode, False, artifact_dir, target)
    print(comparison_text)
    print(action_text)
    print(format_run_artifact_summary(repo, recent_state_path, config_path, artifact_dir, target))
    print_post_success_publish_summary(
        {
            "validation_result": "failed",
            "publish_requested": publish_requested,
            "publish_triggered": False,
            "publish_mode": "validated-run",
            "publish_result": "not_requested" if not publish_requested else "not_requested",
            "publish_reason": (f"publish not run because validation_result=failed" if publish_requested else ""),
            "pr_created_or_reused": False,
            "pr_merged": False,
            "local_main_synced": False,
        }
    )
    print(
        format_final_operator_summary(
            {
                "validation_result": "failed",
                "publish_requested": publish_requested,
                "publish_triggered": False,
                "publish_mode": "validated-run",
                "publish_result": "not_requested",
                "publish_reason": (f"publish not run because validation_result=failed" if publish_requested else ""),
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
