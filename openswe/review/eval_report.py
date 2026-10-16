"""What the reviewer eval's sandbox reports to the dashboard, and where.

Kept deliberately light (no dashboard/server imports) so the eval harness in the
eval sandbox can report progress without importing the FastAPI dashboard. Both
``openswe.review.eval_jobs`` (receiver) and ``evals.reviewer.status_reporter``
(sender) import from here.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_EVAL_PROJECT = "open-swe-evals"

# Under /dashboard/api so a dashboard deployment that fronts the backend forwards it.
REPORT_PATH = "/dashboard/api/reviewer-eval/report"
# The launcher hands the sandbox this URL; the sandbox proxy adds the run's
# token header to requests for its host, so the token never enters the sandbox.
REPORT_URL_ENV = "REVIEWER_EVAL_REPORT_URL"
REPORT_TOKEN_HEADER = "X-Open-SWE-Eval-Token"

LOG_TAIL_CHARS = 12000
ERROR_CHARS = 4000
EXPERIMENT_URL_RE = re.compile(r"https://\S*smith\.langchain\.com/\S+")

# The running eval reports this often; a run is only reconciled as failed once
# its last report is older than the stale threshold, so a brief dashboard/eval
# lag doesn't kill a live run.
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_STALE_SECONDS = 60


class ReviewerEvalReport(BaseModel):
    """One progress report from a running eval; each one doubles as its heartbeat."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["running", "completed", "failed"]
    completed_examples: int = Field(ge=0)
    total_examples: int | None = Field(default=None, ge=0)
    experiment_url: str | None = Field(default=None, max_length=2048)
    log_tail: str | None = Field(default=None, max_length=LOG_TAIL_CHARS)
    error: str | None = Field(default=None, max_length=ERROR_CHARS)
