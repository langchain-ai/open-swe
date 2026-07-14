"""Shared constants for the reviewer-eval status record.

Kept deliberately light (no dashboard/server imports) so the eval harness in the
GitHub Action can publish progress to the store without importing the FastAPI
dashboard. Both ``agent.dashboard.eval_jobs`` (reader) and
``evals.reviewer.store_reporter`` (writer) import from here.
"""

from __future__ import annotations

import re

EVALS_NAMESPACE: list[str] = ["evals"]
REVIEWER_EVAL_KEY = "reviewer"
DEFAULT_EVAL_PROJECT = "open-swe-evals"

_LOG_TAIL_CHARS = 12000
_EXPERIMENT_URL_RE = re.compile(r"https://\S*smith\.langchain\.com/\S+")

# The running Action refreshes the heartbeat this often; a record is only
# reconciled as failed once its heartbeat is older than the stale threshold, so
# a brief dashboard/Action lag doesn't kill a live run.
_HEARTBEAT_INTERVAL_SECONDS = 10
_HEARTBEAT_STALE_SECONDS = 60
