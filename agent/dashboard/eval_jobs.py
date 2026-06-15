"""Kick off and track the reviewer eval from the admin dashboard.

Runs ``evals.reviewer.run_eval`` as a subprocess so its LangSmith tracing
project (``open-swe-evals``) stays isolated from the deployment's production
project, and so a long eval does not block the server event loop. Status is
persisted in the LangGraph store so it survives across dashboard requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import uuid
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
# The owning worker refreshes the heartbeat this often while the subprocess
# runs; a record is only reconciled as failed once its heartbeat is older than
# the stale threshold, so polls on other workers don't kill a live run.
_HEARTBEAT_INTERVAL_SECONDS = 10
_HEARTBEAT_STALE_SECONDS = 60

EvalStatus = Literal["idle", "running", "completed", "failed"]

# Identifies this process so heartbeat ownership can be reasoned about across
# workers that share the persisted record.
_WORKER_ID = uuid.uuid4().hex

# Live subprocess handles keyed by eval name, owned by the worker that launched
# them. The store record is the source of truth across workers/requests.
_PROCS: dict[str, asyncio.subprocess.Process] = {}

# Rolling tail of subprocess output, kept by the owning worker so the heartbeat
# loop can persist a live log tail to the store while the eval runs.
_LOG_BUFFERS: dict[str, str] = {}


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
        "worker_id": None,
        "heartbeat": None,
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


def _heartbeat_age_seconds(record: dict[str, Any]) -> float | None:
    """Seconds since the record's heartbeat, or ``None`` if absent/unparseable."""
    hb = record.get("heartbeat")
    if not isinstance(hb, str) or not hb:
        return None
    try:
        ts = datetime.fromisoformat(hb)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


def _is_heartbeat_fresh(record: dict[str, Any]) -> bool:
    age = _heartbeat_age_seconds(record)
    return age is not None and age <= _HEARTBEAT_STALE_SECONDS


async def get_reviewer_eval_status() -> dict[str, Any]:
    """Return the latest reviewer-eval status, reconciling stale ``running``.

    The owning worker refreshes the record's heartbeat while its subprocess
    runs. A poll from any worker only marks the run failed once the heartbeat
    is stale, so a status check on a worker that doesn't own the process (no
    local handle but a fresh heartbeat) leaves a live run untouched.
    """
    record = await _get_record()
    if record is None:
        return _idle_record()
    if record.get("status") != "running":
        return record
    if _is_locally_running() or _is_heartbeat_fresh(record):
        return record
    return await _put_record(
        {
            **record,
            "status": "failed",
            "finished_at": record.get("finished_at") or _now_iso(),
            "error": "Eval process is no longer tracked (server restarted?).",
        }
    )


async def start_reviewer_eval(
    *,
    limit: int | None,
    created_by: str,
) -> dict[str, Any]:
    """Launch the reviewer eval subprocess and persist a ``running`` record.

    Raises ``RuntimeError`` if an eval is already running on this or another
    worker (detected via a fresh heartbeat on the shared record).
    """
    if _is_locally_running():
        raise RuntimeError("a reviewer eval is already running")
    existing = await _get_record()
    if existing and existing.get("status") == "running" and _is_heartbeat_fresh(existing):
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
            "worker_id": _WORKER_ID,
            "heartbeat": _now_iso(),
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


async def _heartbeat_loop(proc: asyncio.subprocess.Process) -> None:
    """Refresh heartbeat and live log tail while the owned subprocess is alive."""
    while proc.returncode is None:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        if proc.returncode is not None:
            return
        record = await _get_record()
        if not record or record.get("status") != "running":
            return
        await _put_record(
            {
                **record,
                "heartbeat": _now_iso(),
                "log_tail": _LOG_BUFFERS.get(REVIEWER_EVAL_KEY) or record.get("log_tail"),
            }
        )


async def _stream_output(proc: asyncio.subprocess.Process) -> tuple[str, str | None]:
    """Read stdout to EOF, keeping a rolling tail and the last experiment URL.

    The tail is published to ``_LOG_BUFFERS`` as it grows so the heartbeat loop
    can persist it mid-run. Reading in fixed chunks avoids the line-length cap
    that ``StreamReader.readline`` would impose on long log lines.
    """
    tail = ""
    experiment_url: str | None = None
    if proc.stdout is None:
        return tail, experiment_url
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        tail = (tail + chunk.decode("utf-8", errors="replace"))[-_LOG_TAIL_CHARS:]
        urls = _EXPERIMENT_URL_RE.findall(tail)
        if urls:
            experiment_url = urls[-1]
        _LOG_BUFFERS[REVIEWER_EVAL_KEY] = tail
    return tail, experiment_url


async def _monitor(
    proc: asyncio.subprocess.Process,
    *,
    created_by: str,
    limit: int | None,
    project: str,
) -> None:
    heartbeat = asyncio.create_task(_heartbeat_loop(proc))
    tail = ""
    experiment_url: str | None = None
    try:
        tail, experiment_url = await _stream_output(proc)
        await proc.wait()
    except Exception:
        logger.exception("Error while monitoring reviewer eval subprocess")
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        _PROCS.pop(REVIEWER_EVAL_KEY, None)
        _LOG_BUFFERS.pop(REVIEWER_EVAL_KEY, None)

    log_tail = tail or None
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
