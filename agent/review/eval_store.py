"""The reviewer-eval status record: where it lives and what it holds.

The eval runs in the ``Reviewer eval`` GitHub Action and publishes its progress into a
LangGraph store record the dashboard reads. This module is the contract between the
writer (``evals.reviewer.store_reporter``) and the reader (``agent.settings.eval_jobs``);
it stays free of dashboard/server imports so the Action can publish progress without
importing the FastAPI dashboard.
"""

import re
from typing import Literal, TypedDict

from agent.review.eval_config import ReviewerEvalConfig

EVALS_NAMESPACE: list[str] = ["evals"]
REVIEWER_EVAL_KEY = "reviewer"

LOG_TAIL_CHARS = 12000
EXPERIMENT_URL_RE = re.compile(r"https://\S*smith\.langchain\.com/\S+")

# The running Action refreshes the heartbeat this often; a record is only
# reconciled as failed once its heartbeat is older than the stale threshold, so
# a brief dashboard/Action lag doesn't kill a live run.
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_STALE_SECONDS = 60

EvalStatus = Literal["idle", "running", "completed", "failed"]


class EvalProgress(TypedDict):
    completed: int
    total: int | None


class ReviewerEvalRecord(TypedDict):
    name: str
    status: EvalStatus
    run_name: str
    langsmith_project: str
    limit: int | None
    config_snapshot: ReviewerEvalConfig
    started_at: str | None
    finished_at: str | None
    created_by: str | None
    pid: int | None
    exit_code: int | None
    experiment_url: str | None
    error: str | None
    log_tail: str | None
    worker_id: str | None
    heartbeat: str | None
    progress: EvalProgress | None
    github_run_url: str | None
    trigger: str | None
    updated_at: str
