"""Target function for the agent eval.

Runs the coding agent on one benchmark instance, then asks the same thread to
print its diff. The agent is never asked to open a pull request: delivery is a
product surface that writes to the upstream repository, and an eval must not
push anywhere. The thread's sandbox holds the working tree, so a follow-up
message on the same thread is enough to collect the patch.
"""

import logging
import os
import threading
import time
from typing import Any, cast

from langgraph_sdk import get_client

from agent.input_messages import build_run_input

logger = logging.getLogger(__name__)

DEFAULT_AGENT_ASSISTANT_ID = "agent"
DEFAULT_LANGGRAPH_URL = "http://localhost:2024"

_THREAD_IDS: set[str] = set()
_THREAD_IDS_LOCK = threading.Lock()

_COMPLETED = 0
_COMPLETED_LOCK = threading.Lock()


def _record_completed() -> None:
    global _COMPLETED
    with _COMPLETED_LOCK:
        _COMPLETED += 1


def get_completed_count() -> int:
    """Number of examples that have finished so far in this process."""
    with _COMPLETED_LOCK:
        return _COMPLETED


def _record_thread_id(thread_id: str) -> None:
    with _THREAD_IDS_LOCK:
        _THREAD_IDS.add(thread_id)


def drain_thread_ids() -> set[str]:
    """Return and clear thread IDs created by ``solve_task`` so far.

    Used by ``run_eval`` to delete threads after the experiment finishes.
    Underlying provider sandboxes time out via their own TTL — deleting the
    LangGraph thread frees the checkpoint/metadata records, not the sandbox.
    """
    with _THREAD_IDS_LOCK:
        snapshot = set(_THREAD_IDS)
        _THREAD_IDS.clear()
    return snapshot


def get_langgraph_url() -> str:
    return os.getenv("LANGGRAPH_URL", DEFAULT_LANGGRAPH_URL)


def get_agent_assistant_id() -> str:
    return os.getenv("AGENT_ASSISTANT_ID", DEFAULT_AGENT_ASSISTANT_ID)


def get_agent_model_id() -> str | None:
    value = os.getenv("AGENT_EVAL_MODEL_ID")
    return value if value else None


def get_agent_reasoning_effort() -> str | None:
    value = os.getenv("AGENT_EVAL_REASONING_EFFORT")
    return value if value else None


def get_eval_run_source() -> str:
    """Surface the target runs the agent graph under.

    ``eval`` is the default. ``desktop`` is the product's local-execution path:
    it skips GitHub App token resolution, which a deployment without App
    credentials (a local sandbox smoke run) cannot satisfy. Only use it when the
    server's sandbox provider is local — a cloud sandbox still needs the token.
    """
    value = os.getenv("AGENT_EVAL_RUN_SOURCE", "eval")
    return value if value in {"eval", "desktop"} else "eval"


def get_local_project_dir() -> str | None:
    """Directory the agent may work in, for ``desktop``-surface runs.

    The desktop path authenticates the working directory against the server's
    ``OPEN_SWE_LOCAL_PROJECTS_FILE`` allowlist (or its worktrees dir), so this
    must name an allowlisted path. Only used when ``run_source`` is ``desktop``.
    """
    value = os.getenv("AGENT_EVAL_LOCAL_PROJECT_DIR")
    return value if value and value.strip() else None


def _eval_context() -> dict[str, Any]:
    return {
        "sender_id": "system:agent-eval",
        "surface": "eval",
        "kind": "system",
    }


def _eval_systems() -> list[dict[str, Any]]:
    return [
        {
            "id": "system:agent-eval",
            "display_name": "Agent evaluation",
            "platform": "open-swe",
        }
    ]


def _build_configurable(inputs: dict[str, Any]) -> dict[str, Any]:
    repo = inputs.get("repo", "")
    owner, _, name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
    configurable: dict[str, Any] = {
        "__is_for_execution__": True,
        "eval": True,
        "repo": {"owner": owner, "name": name},
        "source": get_eval_run_source(),
    }
    if configurable["source"] == "desktop":
        project_dir = get_local_project_dir()
        if project_dir:
            configurable["local_project_path"] = project_dir
        else:
            raise RuntimeError(
                "run_source 'desktop' requires AGENT_EVAL_LOCAL_PROJECT_DIR: the "
                "desktop path works in an allowlisted local directory"
            )
    model_id = get_agent_model_id()
    if model_id:
        configurable["agent_model_id"] = model_id
    reasoning_effort = get_agent_reasoning_effort()
    if reasoning_effort:
        configurable["agent_effort"] = reasoning_effort
    return configurable


def _solve_prompt(inputs: dict[str, Any]) -> str:
    clone_url = str(inputs.get("clone_url") or "") or f"https://github.com/{inputs['repo']}.git"
    return (
        f"Fix the following issue in the {inputs['repo']} repository.\n\n"
        f"First make sure the repository is checked out in your working directory — "
        f"clone {clone_url} if it is not there yet — then "
        f"check out the exact base commit so the working tree matches the reported state:\n"
        f"    git fetch origin {inputs['base_commit']} && git checkout {inputs['base_commit']}\n\n"
        f"Issue:\n{inputs['problem_statement']}\n\n"
        f"Requirements:\n"
        f"- Make the code changes needed to fix the issue.\n"
        f"- Validate the change with the repository's own tests where practical.\n"
        f"- Do NOT open a pull request, push any branch, or otherwise write to GitHub. "
        f"Another process collects your diff from this sandbox.\n"
    )


def _extract_prompt(inputs: dict[str, Any]) -> str:
    # Diff against the base commit, not the index: the agent may have committed
    # (the desktop flow encourages it), and `git diff --cached` alone would then
    # come back empty. `git add -A` first so unstaged work is included too.
    return (
        f"Run `git add -A` in the repository working directory, then output the complete "
        f"`git diff --cached {inputs['base_commit']}`. Respond with the raw diff and nothing "
        f"else — no prose, no code fences. If you made no changes, respond with an empty "
        f"message."
    )


def _extract_final_text(result: Any) -> str:
    """Text of the last AI message in a run result, if any."""
    if not isinstance(result, dict):
        return ""
    texts: list[str] = []
    for message in result.get("messages") or []:
        if not isinstance(message, dict) or message.get("type") != "ai":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            texts.append("".join(parts))
    for text in reversed(texts):
        if text.strip():
            return text
    return ""


def _count_model_turns(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    return sum(
        1
        for message in result.get("messages") or []
        if isinstance(message, dict) and message.get("type") == "ai"
    )


async def _wait(
    client: Any, thread_id: str, prompt: str, configurable: dict[str, Any]
) -> dict[str, Any]:
    result = await client.runs.wait(
        thread_id,
        assistant_id=get_agent_assistant_id(),
        input=build_run_input(prompt, _eval_context(), systems=_eval_systems()),
        config={"configurable": configurable},
    )
    return cast(dict[str, Any], result)


async def solve_task(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith target: run the coding agent on one benchmark instance."""
    repo = inputs.get("repo", "")
    instance_id = inputs.get("instance_id", "")
    logger.info("Starting agent eval example: instance=%s repo=%s", instance_id, repo)
    started = time.monotonic()
    client = get_client(url=get_langgraph_url())
    thread = await client.threads.create()
    thread_id: str = thread["thread_id"]
    _record_thread_id(thread_id)
    try:
        configurable = _build_configurable(inputs)
        await _wait(client, thread_id, _solve_prompt(inputs), configurable)
        result = await _wait(client, thread_id, _extract_prompt(inputs), configurable)
        patch = _extract_final_text(result)
        steps = _count_model_turns(result)
        wall_clock_seconds = round(time.monotonic() - started, 3)
        logger.info(
            "Finished agent eval example: instance=%s repo=%s patch_chars=%d steps=%d "
            "wall_clock_seconds=%s thread_id=%s",
            instance_id,
            repo,
            len(patch),
            steps,
            wall_clock_seconds,
            thread_id,
        )
        _record_completed()
        return {
            "instance_id": instance_id,
            "repo": repo,
            "patch": patch,
            "steps": steps,
            "wall_clock_seconds": wall_clock_seconds,
        }
    except Exception:
        logger.exception("Agent eval example failed: instance=%s repo=%s", instance_id, repo)
        raise
