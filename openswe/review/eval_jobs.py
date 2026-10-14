"""Track the reviewer eval for the admin dashboard.

``start_reviewer_eval`` launches the eval detached in a LangSmith sandbox,
isolated from the serving deployment. Each launch is a ``reviewer_eval_run``
row, and the dashboard shows the latest. The harness posts its progress to
``REPORT_PATH`` under the run's token (see ``evals.reviewer.status_reporter``);
a run whose reports go stale (e.g. the sandbox stopped) is reconciled to
``failed``.
"""

import hashlib
import secrets
import shlex
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal, Self, TypedDict
from urllib.parse import urlsplit
from uuid import UUID, uuid7

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import ColumnElement, ForeignKey, ScalarSelect, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.review.eval_report import (
    DEFAULT_EVAL_PROJECT,
    HEARTBEAT_STALE_SECONDS,
    REPORT_PATH,
    REPORT_TOKEN_HEADER,
    REPORT_URL_ENV,
    ReviewerEvalReport,
)
from openswe.users.records import github_login_of
from openswe.utils.build_info import backend_build_info
from openswe.utils.dashboard_links import dashboard_api_base_url
from openswe.utils.gateway import gateway_base_url
from openswe.utils.json_types import JsonObject

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


EVAL_CONFIG = TypeAdapter(ReviewerEvalConfig)
ACTIVE_STATUSES: frozenset[str] = frozenset({"starting", "running"})
# A launched sandbox clones and syncs before the harness sends its first report.
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


class ReviewerEvalProgress(BaseModel):
    completed: int
    total: int | None


class ReviewerEvalStatus(BaseModel):
    """The latest reviewer eval, as the admin dashboard shows it."""

    status: EvalStatus
    run_name: str
    langsmith_project: str
    limit: int | None = None
    config_snapshot: ReviewerEvalConfig
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: str | None = None
    experiment_url: str | None = None
    error: str | None = None
    log_tail: str | None = None
    progress: ReviewerEvalProgress | None = None
    worker_id: str | None = None

    @classmethod
    def idle(cls) -> Self:
        config = resolve_eval_config()
        return cls(
            status="idle",
            run_name=config["experiment_prefix"],
            langsmith_project=config["langsmith_project"],
            config_snapshot=config,
        )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ReviewerEvalRun(Base):
    """One launched reviewer eval."""

    __tablename__ = "reviewer_eval_run"

    status: Mapped[str]
    config: Mapped[JsonObject] = mapped_column(JSONB)
    created_at: Mapped[datetime]
    heartbeat_at: Mapped[datetime | None]
    example_limit: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    sandbox_name: Mapped[str | None] = mapped_column(default=None)
    report_token_hash: Mapped[str | None] = mapped_column(default=None)
    experiment_url: Mapped[str | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    log_tail: Mapped[str | None] = mapped_column(default=None)
    completed_examples: Mapped[int | None] = mapped_column(default=None)
    total_examples: Mapped[int | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def latest(cls) -> Self | None:
        """The run started last."""
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).order_by(cls.created_at.desc(), cls.id.desc()).limit(1)
            )

    @classmethod
    async def begin(
        cls,
        config: ReviewerEvalConfig,
        limit: int | None,
        actor: UUID | ScalarSelect[UUID] | None,
    ) -> tuple[ReviewerEvalRun, str]:
        """Record a run the person ``actor`` names is launching, returning it and the token
        its sandbox reports with."""
        token = secrets.token_urlsafe(32)
        async with postgres.session() as session:
            run = await session.scalar(
                insert(ReviewerEvalRun)
                .values(
                    id=uuid7(),
                    status="starting",
                    config=dict(config),
                    heartbeat_at=func.clock_timestamp(),
                    example_limit=limit,
                    report_token_hash=_token_hash(token),
                    created_by=actor,
                    updated_by=actor,
                )
                .returning(ReviewerEvalRun)
            )
        if run is None:
            raise RuntimeError("the reviewer eval run was not recorded")
        return run, token

    async def _update(
        self, *conditions: ColumnElement[bool], updated_by: UUID | None, **values: object
    ) -> ReviewerEvalRun | None:
        async with postgres.session() as session:
            return await session.scalar(
                update(ReviewerEvalRun)
                .where(ReviewerEvalRun.id == self.id, *conditions)
                .values(**values, updated_by=updated_by, updated_at=func.clock_timestamp())
                .returning(ReviewerEvalRun),
                execution_options={"synchronize_session": False},
            )

    async def launched(self, sandbox_name: str) -> ReviewerEvalRun:
        """Record the sandbox the launching person's request started."""
        return await self._update(updated_by=self.created_by, sandbox_name=sandbox_name) or self

    async def fail(self, error: str) -> ReviewerEvalRun:
        """Mark the run failed unless it has finished since it was read."""
        failed = await self._update(
            ReviewerEvalRun.status.in_(ACTIVE_STATUSES),
            updated_by=None,
            status="failed",
            finished_at=func.coalesce(ReviewerEvalRun.finished_at, func.clock_timestamp()),
            error=error,
        )
        if failed is not None:
            return failed
        async with postgres.session() as session:
            return await session.get(ReviewerEvalRun, self.id, populate_existing=True) or self

    @classmethod
    async def record_report(cls, token: str, report: ReviewerEvalReport) -> bool:
        """Apply a report from the sandbox of the run ``token`` belongs to; ``False`` for none."""
        finished = report.status != "running"
        async with postgres.session() as session:
            run_id = await session.scalar(
                update(cls)
                .where(cls.report_token_hash == _token_hash(token))
                .values(
                    status=report.status,
                    completed_examples=report.completed_examples,
                    total_examples=report.total_examples,
                    experiment_url=report.experiment_url,
                    log_tail=report.log_tail,
                    error=report.error,
                    heartbeat_at=func.clock_timestamp(),
                    finished_at=func.clock_timestamp() if finished else None,
                    updated_by=None,
                    updated_at=func.clock_timestamp(),
                )
                .returning(cls.id),
                execution_options={"synchronize_session": False},
            )
        return run_id is not None

    def heartbeat_is_fresh(self) -> bool:
        if self.heartbeat_at is None:
            return False
        limit = STARTING_STALE_SECONDS if self.status == "starting" else HEARTBEAT_STALE_SECONDS
        return (datetime.now(UTC) - self.heartbeat_at).total_seconds() <= limit

    def stale_error(self) -> str:
        if self.status == "starting":
            return f"Eval never reported in; see {LOG_PATH} in sandbox {self.sandbox_name}."
        return "Eval process is no longer tracked (eval sandbox stopped?)."

    async def status_view(self) -> ReviewerEvalStatus:
        """The run as the dashboard shows it, naming its launcher by GitHub login."""
        async with postgres.session() as session:
            launcher = await session.scalar(
                select(github_login_of(ReviewerEvalRun.created_by)).where(
                    ReviewerEvalRun.id == self.id
                )
            )
        config = EVAL_CONFIG.validate_python(self.config)
        progress = (
            None
            if self.completed_examples is None
            else ReviewerEvalProgress(completed=self.completed_examples, total=self.total_examples)
        )
        return ReviewerEvalStatus.model_validate(
            {
                "status": self.status,
                "run_name": config["experiment_prefix"],
                "langsmith_project": config["langsmith_project"],
                "limit": self.example_limit,
                "config_snapshot": config,
                "started_at": self.created_at,
                "finished_at": self.finished_at,
                "created_by": launcher,
                "experiment_url": self.experiment_url,
                "error": self.error,
                "log_tail": self.log_tail,
                "progress": progress,
                "worker_id": self.sandbox_name,
            }
        )


async def get_reviewer_eval_status() -> ReviewerEvalStatus:
    """Return the latest reviewer-eval status, reconciling a stale active run.

    The eval sandbox reports while it runs, and each report is a heartbeat. A
    poll only marks the run failed once the heartbeat is stale, so a healthy run
    is left untouched and a stopped sandbox surfaces as ``failed`` within the
    stale threshold.
    """
    run = await ReviewerEvalRun.latest()
    if run is None:
        return ReviewerEvalStatus.idle()
    if run.status in ACTIVE_STATUSES and not run.heartbeat_is_fresh():
        run = await run.fail(run.stale_error())
    return await run.status_view()


def _proxy_rules(headers: Iterable[tuple[str, str, str]]) -> list[dict[str, object]]:
    """One rule per host, adding each ``(url, header, value)`` to requests for the url's host.

    The first value given for a header on a host wins.
    """
    by_host: dict[str, dict[str, str]] = {}
    for url, name, value in headers:
        by_host.setdefault(urlsplit(url).netloc, {}).setdefault(name, value)
    return [
        {
            "name": f"reviewer-eval-{index}",
            "match_hosts": [host],
            "headers": [
                {"name": name, "type": "opaque", "value": value}
                for name, value in host_headers.items()
            ],
        }
        for index, (host, host_headers) in enumerate(by_host.items())
    ]


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
    config: ReviewerEvalConfig, limit: int | None, report_token: str, ref: str
) -> str:
    """Run the harness detached in a sandbox that stops itself when the eval exits.

    API keys and the report token never enter the sandbox: its proxy injects
    them on the wire.
    """
    from openswe.sandboxes.providers.langsmith import get_async_sandbox_client

    api_key = ENV.LANGSMITH_API_KEY.get()
    endpoint = ENV.LANGSMITH_ENDPOINT.get().rstrip("/")
    langgraph_url = config["langgraph_url"] or ENV.LANGGRAPH_URL.get()
    gateway = gateway_base_url()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or api_key
    report_url = dashboard_api_base_url() + REPORT_PATH
    env = {
        "LANGSMITH_API_KEY": KEY_PLACEHOLDER,
        "LANGSMITH_ENDPOINT": endpoint,
        "LANGSMITH_GATEWAY_BASE_URL": gateway,
        "LANGGRAPH_URL": langgraph_url,
        REPORT_URL_ENV: report_url,
    }
    rules = _proxy_rules(
        [
            (endpoint, "x-api-key", api_key),
            (langgraph_url, "x-api-key", api_key),
            (gateway, "x-api-key", gateway_key),
            (report_url, REPORT_TOKEN_HEADER, report_token),
        ]
    )
    async with get_async_sandbox_client() as client:
        sandbox = await client.create_sandbox(
            idle_ttl_seconds=0,
            delete_after_stop_seconds=DELETE_AFTER_STOP_SECONDS,
            proxy_config={"rules": rules},
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
    config: ReviewerEvalConfig, limit: int | None, actor: UUID | ScalarSelect[UUID] | None
) -> ReviewerEvalStatus:
    """Launch a reviewer eval in a sandbox as the person ``actor`` names; raises
    ``RuntimeError`` if one can't start."""
    if (await get_reviewer_eval_status()).status in ACTIVE_STATUSES:
        raise RuntimeError("a reviewer eval is already running")
    ref = backend_build_info()["commit"]
    if not ref:
        raise RuntimeError("the deployment's commit is unknown, so the eval can't match its code")
    run, report_token = await ReviewerEvalRun.begin(config, limit, actor)
    try:
        sandbox_name = await _launch_sandbox(config, limit, report_token, ref)
    except Exception as exc:
        await run.fail(f"Couldn't launch the eval sandbox: {exc}")
        raise
    return await (await run.launched(sandbox_name)).status_view()
