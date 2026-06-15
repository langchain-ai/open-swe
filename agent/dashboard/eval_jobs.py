"""Kick off and track the reviewer eval from the admin dashboard.

Runs ``evals.reviewer.run_eval`` as a subprocess so its LangSmith tracing
project (``open-swe-evals``) stays isolated from the deployment's production
project, and so a long eval does not block the server event loop. Status is
persisted in the LangGraph store so it survives across dashboard requests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

EVALS_NAMESPACE: list[str] = ["evals"]
REVIEWER_EVAL_KEY = "reviewer"
DEFAULT_EVAL_PROJECT = "open-swe-evals"
_MODULE = "evals.reviewer.run_eval"
_LOG_TAIL_CHARS = 4000
_EXPERIMENT_URL_RE = re.compile(r"https://\S*smith\.langchain\.com/\S+")

EvalStatus = Literal["idle", "running", "completed", "failed"]

# Live subprocess handles keyed by eval name, owned by the worker that launched
# them. The store record is the source of truth across workers/requests.
_PROCS: dict[str, asyncio.subprocess.Process] = {}


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_langgraph_url() -> str | None:
    return os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD")


def _eval_project() -> str:
    return os.environ.get("EVAL_LANGSMITH_PROJECT") or DEFAULT_EVAL_PROJECT


def _idle_record() -> dict[str, Any]:
    return {
        "name": REVIEWER_EVAL_KEY,
        "status": "idle",
        "langsmith_project": _eval_project(),
        "limit": None,
        "started_at": None,
        "finished_at": None,
        "created_by": None,
        "pid": None,
        "exit_code": None,
        "experiment_url": None,
        "error": None,
        "log_tail": None,
        "updated_at": _now_iso(),
    }


async def _get_record() -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY)
    except Exception as e:
        logger.debug("store get_item failed for reviewer eval: %s", e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _put_record(record: dict[str, Any]) -> dict[str, Any]:
    record = {**record, "updated_at": _now_iso()}
    try:
        await _client().store.put_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY, record)
    except Exception:
        logger.exception("Failed to persist reviewer eval status")
    return record


def _is_locally_running() -> bool:
    proc = _PROCS.get(REVIEWER_EVAL_KEY)
    return proc is not None and proc.returncode is None


async def get_reviewer_eval_status() -> dict[str, Any]:
    """Return the latest reviewer-eval status, reconciling stale ``running``.

    If the store says ``running`` but this worker has no live process, the
    launching worker is gone (e.g. after a restart), so the run is reported as
    failed rather than stuck running forever.
    """
    record = await _get_record()
    if record is None:
        return _idle_record()
    if record.get("status") == "running" and not _is_locally_running():
        record = await _put_record(
            {
                **record,
                "status": "failed",
                "finished_at": record.get("finished_at") or _now_iso(),
                "error": "Eval process is no longer tracked (server restarted?).",
            }
        )
    return record


async def start_reviewer_eval(
    *,
    limit: int | None,
    created_by: str,
) -> dict[str, Any]:
    """Launch the reviewer eval subprocess and persist a ``running`` record.

    Raises ``RuntimeError`` if an eval is already running on this worker.
    """
    if _is_locally_running():
        raise RuntimeError("a reviewer eval is already running")

    project = _eval_project()
    cmd = [sys.executable, "-m", _MODULE]
    if limit is not None and limit > 0:
        cmd += ["--limit", str(limit)]

    env = {
        **os.environ,
        "LANGSMITH_PROJECT": project,
        "LANGCHAIN_PROJECT": project,
        "LANGSMITH_TRACING": "true",
    }
    langgraph_url = _resolve_langgraph_url()
    if langgraph_url:
        env["LANGGRAPH_URL"] = langgraph_url

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_repo_root()),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        logger.exception("Failed to launch reviewer eval subprocess")
        return await _put_record(
            {
                **_idle_record(),
                "status": "failed",
                "finished_at": _now_iso(),
                "created_by": created_by,
                "error": f"Failed to launch eval: {exc}",
            }
        )

    _PROCS[REVIEWER_EVAL_KEY] = proc
    record = await _put_record(
        {
            **_idle_record(),
            "status": "running",
            "langsmith_project": project,
            "limit": limit,
            "started_at": _now_iso(),
            "finished_at": None,
            "created_by": created_by,
            "pid": proc.pid,
        }
    )
    asyncio.create_task(_monitor(proc, created_by=created_by, limit=limit, project=project))
    return record


async def cancel_reviewer_eval() -> dict[str, Any]:
    """Terminate a locally-running reviewer eval, if any."""
    proc = _PROCS.get(REVIEWER_EVAL_KEY)
    if proc is not None and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    record = await _get_record() or _idle_record()
    return await _put_record(
        {
            **record,
            "status": "failed",
            "finished_at": _now_iso(),
            "error": "Eval cancelled by an admin.",
        }
    )


async def _monitor(
    proc: asyncio.subprocess.Process,
    *,
    created_by: str,
    limit: int | None,
    project: str,
) -> None:
    output = b""
    try:
        if proc.stdout is not None:
            output = await proc.stdout.read()
        await proc.wait()
    except Exception:
        logger.exception("Error while monitoring reviewer eval subprocess")
    finally:
        _PROCS.pop(REVIEWER_EVAL_KEY, None)

    text = output.decode("utf-8", errors="replace")
    log_tail = text[-_LOG_TAIL_CHARS:] if text else None
    urls = _EXPERIMENT_URL_RE.findall(text)
    experiment_url = urls[-1] if urls else None
    exit_code = proc.returncode
    status: EvalStatus = "completed" if exit_code == 0 else "failed"
    error = None if status == "completed" else f"Eval exited with code {exit_code}."

    record = await _get_record() or _idle_record()
    await _put_record(
        {
            **record,
            "status": status,
            "langsmith_project": project,
            "limit": limit,
            "created_by": created_by,
            "finished_at": _now_iso(),
            "pid": proc.pid,
            "exit_code": exit_code,
            "experiment_url": experiment_url,
            "error": error,
            "log_tail": log_tail,
        }
    )
