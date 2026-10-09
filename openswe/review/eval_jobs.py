"""Track the reviewer eval for the admin web.

``start_reviewer_eval`` launches the eval detached in a LangSmith sandbox,
isolated from the serving deployment. The harness reports progress
into a LangGraph store record (namespace ``["evals"]``, key ``"reviewer"``) via
``evals.reviewer.store_reporter``; this module reads that record for the
web and reconciles a run whose heartbeat has gone stale (e.g. the sandbox
stopped) to ``failed``.
"""

import logging
import shlex
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

from openswe.config import ENV
from openswe.review.eval_store import (
    DEFAULT_EVAL_PROJECT,
    EVALS_NAMESPACE,
    HEARTBEAT_STALE_SECONDS,
    REVIEWER_EVAL_KEY,
)
from openswe.store import get_value, now_iso, put_value
from openswe.utils.build_info import backend_build_info
from openswe.utils.gateway import gateway_base_url

logger = logging.getLogger(__name__)

EvalStatus = Literal["idle", "starting", "running", "completed", "failed"]
ScoreMode = Literal["all_findings", "surfaced_findings"]
Severity = Literal["low", "medium", "high", "critical"]


class ReviewerEvalConfig(TypedDict):
    dataset_name: str
    experiment_prefix: str
    max_concurrency: int
    langsmith_project: str
    langgraph_url: str
    assistant_id: str
    model_id: str
    reasoning_effort: str
    score_mode: ScoreMode
    severity_threshold: Severity


ACTIVE_STATUSES: frozenset[str] = frozenset({"starting", "running"})
# A launched sandbox clones and syncs before the harness writes its first heartbeat.
STARTING_STALE_SECONDS = 15 * 60
EVAL_TIMEOUT_SECONDS = 8 * 60 * 60
DELETE_AFTER_STOP_SECONDS = 7 * 24 * 60 * 60
REPO_URL = "https://github.com/langchain-ai/open-swe"
KEY_PLACEHOLDER = "sandbox-proxy-injected"
LOG_PATH = "/root/reviewer-eval.log"
_HARNESS_ARGS = (
    "dataset_name",
    "experiment_prefix",
    "max_concurrency",
    "langsmith_project",
    "assistant_id",
    "model_id",
    "reasoning_effort",
    "score_mode",
    "severity_threshold",
)

DEFAULT_REVIEWER_EVAL_CONFIG: ReviewerEvalConfig = {
    "dataset_name": "openswe-reviewer-v2",
    "experiment_prefix": "openswe-review-confidence",
    "max_concurrency": 5,
    "langsmith_project": DEFAULT_EVAL_PROJECT,
    "langgraph_url": "",
    "assistant_id": "reviewer",
    "model_id": "anthropic:claude-opus-5-5",
    "reasoning_effort": "high",
    "score_mode": "surfaced_findings",
    "severity_threshold": "low",
}


def _resolve_langgraph_url() -> str | None:
    return ENV.LANGGRAPH_URL.optional()


def _eval_project() -> str:
    return ENV.EVAL_LANGSMITH_PROJECT.optional() or DEFAULT_EVAL_PROJECT


def resolve_eval_config(config: ReviewerEvalConfig | None = None) -> ReviewerEvalConfig:
    resolved: ReviewerEvalConfig = {
        **DEFAULT_REVIEWER_EVAL_CONFIG,
        "langsmith_project": _eval_project(),
        "langgraph_url": _resolve_langgraph_url() or "",
    }
    if config is not None:
        resolved.update(config)
    return resolved


def _idle_record() -> dict[str, Any]:
    config = resolve_eval_config()
    return {
        "name": REVIEWER_EVAL_KEY,
        "status": "idle",
        "run_name": config["experiment_prefix"],
        "langsmith_project": config["langsmith_project"],
        "limit": None,
        "config_snapshot": config,
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
        "progress": None,
        "trigger": None,
        "updated_at": now_iso(),
    }


async def _get_record() -> dict[str, Any] | None:
    return await get_value(EVALS_NAMESPACE, REVIEWER_EVAL_KEY)


async def _put_record(record: dict[str, Any]) -> dict[str, Any]:
    record = {**record, "updated_at": now_iso()}
    await put_value(EVALS_NAMESPACE, REVIEWER_EVAL_KEY, record)
    return record


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
    limit = (
        STARTING_STALE_SECONDS if record.get("status") == "starting" else HEARTBEAT_STALE_SECONDS
    )
    return age is not None and age <= limit


async def get_reviewer_eval_status() -> dict[str, Any]:
    """Return the latest reviewer-eval status, reconciling a stale ``running``.

    The eval sandbox refreshes the record's heartbeat while it runs. A poll
    only marks the run failed once the heartbeat is stale, so a healthy run is
    left untouched and a stopped sandbox surfaces as ``failed`` within the stale
    threshold.
    """
    record = await _get_record()
    if record is None:
        return _idle_record()
    if record.get("status") not in ACTIVE_STATUSES:
        return record
    if _is_heartbeat_fresh(record):
        return record
    return await _fail(record, _stale_error(record))


def _stale_error(record: dict[str, Any]) -> str:
    if record.get("status") == "starting":
        return f"Eval never reported in; see {LOG_PATH} in sandbox {record.get('worker_id')}."
    return "Eval process is no longer tracked (eval sandbox stopped?)."


async def _fail(record: dict[str, Any], error: str) -> dict[str, Any]:
    finished = record.get("finished_at") or now_iso()
    return await _put_record(
        {**record, "status": "failed", "finished_at": finished, "error": error}
    )


def _key_rule(name: str, urls: list[str], key: str) -> dict[str, Any]:
    return {
        "name": name,
        "match_hosts": [urlsplit(url).netloc for url in urls],
        "headers": [{"name": "x-api-key", "type": "opaque", "value": key}],
    }


def _harness_args(config: ReviewerEvalConfig, limit: int | None) -> list[str]:
    args: list[str] = []
    for key in _HARNESS_ARGS:
        args += [f"--{key.replace('_', '-')}", str(config[key])]
    if reviewer_project := ENV.LANGSMITH_PROJECT.optional():
        args += ["--reviewer-langsmith-project", reviewer_project]
    return args + (["--limit", str(limit)] if limit else [])


def _sandbox_command(ref: str, args: list[str], stop_url: str) -> str:
    script = (
        'export PATH="/root/.local/bin:$PATH"; '
        "command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh; "
        "git init -q /root/open-swe && cd /root/open-swe && "
        f"git fetch -q --depth 1 {REPO_URL} {shlex.quote(ref)} && git checkout -q FETCH_HEAD && "
        f"uv sync --locked && timeout {EVAL_TIMEOUT_SECONDS} "
        f"uv run python -m evals.reviewer.run_eval {shlex.join(args)}"
    )
    stop = (
        f"curl -fsS -X POST -H 'x-api-key: {KEY_PLACEHOLDER}' "
        f"-H 'content-type: application/json' -d '{{}}' {shlex.quote(stop_url)}"
    )
    return f"({script}) > {LOG_PATH} 2>&1; {stop}"


async def _launch_sandbox(
    config: ReviewerEvalConfig, limit: int | None, created_by: str | None, ref: str
) -> str:
    """Run the harness detached in a sandbox that stops itself when the eval exits.

    API keys never enter the sandbox: its proxy injects them on the wire.
    """
    from openswe.sandboxes.providers.langsmith import get_async_sandbox_client

    api_key = ENV.LANGSMITH_API_KEY.get()
    endpoint = ENV.LANGSMITH_ENDPOINT.get().rstrip("/")
    langgraph_url = config["langgraph_url"] or ENV.LANGGRAPH_URL.get()
    gateway = gateway_base_url()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or api_key
    env = {
        "LANGSMITH_API_KEY": KEY_PLACEHOLDER,
        "LANGSMITH_ENDPOINT": endpoint,
        "LANGSMITH_GATEWAY_BASE_URL": gateway,
        "LANGGRAPH_URL": langgraph_url,
        "REVIEWER_EVAL_REPORT_STORE": "1",
        "REVIEWER_EVAL_CREATED_BY": created_by or "",
    }
    async with get_async_sandbox_client() as client:
        sandbox = await client.create_sandbox(
            idle_ttl_seconds=0,
            delete_after_stop_seconds=DELETE_AFTER_STOP_SECONDS,
            proxy_config={
                "rules": [
                    _key_rule("langsmith", [endpoint, langgraph_url], api_key),
                    _key_rule("gateway", [gateway], gateway_key),
                ]
            },
            run_config={"env_vars": env},
            timeout=180,
        )
        stop_url = f"{endpoint}/v2/sandboxes/boxes/{sandbox.name}/stop"
        try:
            await sandbox.run(
                _sandbox_command(ref, _harness_args(config, limit), stop_url),
                timeout=EVAL_TIMEOUT_SECONDS + 3600,
                idle_timeout=-1,
                wait=False,
            )
        except Exception:
            await client.delete_sandbox(sandbox.name)
            raise
    return sandbox.name


async def start_reviewer_eval(
    config: ReviewerEvalConfig, limit: int | None, created_by: str | None
) -> dict[str, Any]:
    """Launch a reviewer eval in a sandbox; raises ``RuntimeError`` if one can't start."""
    if (await get_reviewer_eval_status()).get("status") in ACTIVE_STATUSES:
        raise RuntimeError("a reviewer eval is already running")
    ref = backend_build_info()["commit"]
    if not ref:
        raise RuntimeError("the deployment's commit is unknown, so the eval can't match its code")
    started = now_iso()
    record = await _put_record(
        {
            **_idle_record(),
            "status": "starting",
            "run_name": config["experiment_prefix"],
            "langsmith_project": config["langsmith_project"],
            "limit": limit,
            "config_snapshot": config,
            "started_at": started,
            "created_by": created_by,
            "heartbeat": started,
            "trigger": "sandbox",
        }
    )
    try:
        sandbox_name = await _launch_sandbox(config, limit, created_by, ref)
    except Exception as exc:
        await _fail(record, f"Couldn't launch the eval sandbox: {exc}")
        raise
    return await _put_record({**record, "worker_id": sandbox_name})
