"""LangSmith target that runs the Codex CLI reviewer (`codex exec review`) on one PR.

Findings are read from the session rollout's structured review output and
normalized into the same ``{file, line, body, severity}`` shape as ``target.py``.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from evals.reviewer.judge import ReviewComment
from openswe.utils.gateway import gateway_env_default, gateway_overrides

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("CODEX_EVAL_CACHE_DIR", Path.home() / ".cache" / "openswe-codex-eval"))
CODEX_HOME = Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))
_GATEWAY_KEY_ENV = "CODEX_EVAL_GATEWAY_API_KEY"
_SEVERITIES = ("critical", "high", "medium", "low")
_repo_locks: dict[str, asyncio.Lock] = {}


class LineRange(TypedDict):
    start: int
    end: int


class CodeLocation(TypedDict):
    absolute_file_path: str
    line_range: LineRange


class CodexFinding(TypedDict):
    title: str
    body: str
    priority: NotRequired[int | None]
    code_location: CodeLocation


class CodexReviewOutput(TypedDict, total=False):
    findings: list[CodexFinding]
    overall_correctness: str
    overall_explanation: str


async def _run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} exited {proc.returncode}: {err.decode()[-2000:]}")
    return out.decode()


async def _git(mirror: Path, *args: str) -> None:
    await _run("git", "-C", str(mirror), *args)


async def _add_worktree(repo: str, base_sha: str, head_sha: str, dest: Path) -> Path:
    mirror = CACHE_DIR / repo.replace("/", "__")
    async with _repo_locks.setdefault(repo, asyncio.Lock()):
        if not mirror.exists():
            url = f"https://github.com/{repo}.git"
            await _run("git", "clone", "--filter=blob:none", "--no-checkout", url, str(mirror))
        await _git(mirror, "fetch", "origin", base_sha, head_sha)
        await _git(mirror, "worktree", "add", "--detach", str(dest), head_sha)
    return mirror


def _codex_command(base_sha: str) -> tuple[list[str], dict[str, str]]:
    args = ["codex", "exec", "review", "--json", "--base", base_sha]
    env = dict(os.environ)
    if model := os.getenv("CODEX_EVAL_MODEL"):
        args += ["-m", model]
    if effort := os.getenv("CODEX_EVAL_REASONING_EFFORT"):
        args += ["-c", f'model_reasoning_effort="{effort}"']
    gateway = gateway_overrides("openai:codex") if gateway_env_default() else None
    if gateway:
        env[_GATEWAY_KEY_ENV] = str(gateway["api_key"])
        provider = (
            f'{{name="LangSmith Gateway", base_url="{gateway["base_url"]}", '
            f'env_key="{_GATEWAY_KEY_ENV}", wire_api="responses"}}'
        )
        args += ["-c", "model_provider=langsmith", "-c", f"model_providers.langsmith={provider}"]
    return args, env


def _thread_id(stdout: str) -> str:
    for line in stdout.splitlines():
        event = json.loads(line)
        if event.get("type") == "thread.started":
            return str(event["thread_id"])
    raise RuntimeError("codex exec review emitted no thread.started event")


def _review_output(thread_id: str) -> CodexReviewOutput:
    for path in CODEX_HOME.glob(f"sessions/**/rollout-*{thread_id}.jsonl"):
        for line in path.read_text().splitlines():
            item = json.loads(line).get("payload", {}).get("item") or {}
            if item.get("type") == "ExitedReviewMode":
                return cast(CodexReviewOutput, item.get("review_output") or {})
    raise RuntimeError(f"No review output in Codex rollout for thread {thread_id}")


def _to_comment(finding: CodexFinding, root: Path) -> ReviewComment:
    location = finding["code_location"]
    path = Path(location["absolute_file_path"])
    priority = finding.get("priority")
    return {
        "file": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
        "line": location["line_range"]["end"],
        "body": f"{finding['title']}\n\n{finding['body']}",
        "severity": _SEVERITIES[min(max(priority if priority is not None else 3, 0), 3)],
    }


async def review_pr_codex(inputs: dict[str, str | int]) -> dict[str, object]:
    """LangSmith target: run ``codex exec review`` on one PR checked out at its head SHA."""
    repo, base_sha, head_sha = str(inputs["repo"]), str(inputs["base_sha"]), str(inputs["head_sha"])
    with tempfile.TemporaryDirectory(prefix="codex-review-") as tmp:
        root = Path(tmp).resolve() / "repo"
        mirror = await _add_worktree(repo, base_sha, head_sha, root)
        try:
            args, env = _codex_command(base_sha)
            thread_id = _thread_id(await _run(*args, cwd=root, env=env))
            output = _review_output(thread_id)
        finally:
            await _git(mirror, "worktree", "remove", "--force", str(root))
    findings = output.get("findings") or []
    if not findings and not output.get("overall_correctness"):
        logger.warning("Codex review output was not structured", extra={"thread_id": thread_id})
    return {
        "pr_url": inputs.get("pr_url", ""),
        "codex_thread_id": thread_id,
        "overall_correctness": output.get("overall_correctness", ""),
        "comments": [_to_comment(finding, root) for finding in findings],
    }
