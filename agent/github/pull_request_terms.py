"""The pull-request vocabulary the tables and the dashboard readers share.

Both dashboard reads resolve a pull request out of PostgreSQL before falling
back to GitHub, so the tables classify a stored check exactly as a live read
classifies the payload it came from. Holding that vocabulary apart from either
reader is what lets the tables stay independent of them.

Identity parsing belongs here for the same reason: ``owner``/``repo`` arrive
from the client and are interpolated into GitHub request paths, so every caller
validates them against GitHub's own naming rules first.
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Literal

from agent.config import ENV

CheckState = Literal["failing", "passing", "pending", "unknown"]

FAILING_CHECK_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)
INCONCLUSIVE_CHECK_CONCLUSIONS = frozenset({"cancelled", "stale", "skipped", "neutral"})
FAILING_STATUS_STATES = frozenset({"failure", "error"})

OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40,64}")

DEFAULT_MAX_AGE_SECONDS = 600


def parse_identity(full_name: object, number: object) -> tuple[str, str, int] | None:
    """``(owner, repo, number)`` when both components are safe in a request path."""
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        return None
    owner, repo = full_name.split("/", 1)
    if (
        not OWNER_PATTERN.fullmatch(owner)
        or not REPO_PATTERN.fullmatch(repo)
        or repo in {".", ".."}
    ):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return owner, repo, number


def identity_key(identity: tuple[str, str, int]) -> tuple[str, str, int]:
    """``identity`` as GitHub resolves it: repository paths are case-insensitive."""
    owner, repo, number = identity
    return owner.lower(), repo.lower(), number


def pull_request_identity(record: object) -> tuple[str, str, int] | None:
    """Identity of a pull request recorded in thread metadata."""
    if not isinstance(record, Mapping):
        return None
    return parse_identity(record.get("repo_full_name"), record.get("number"))


def pull_request_max_age() -> timedelta:
    """How old a stored pull request may be before the dashboard re-reads GitHub."""
    seconds = ENV.PULL_REQUEST_STATUS_MAX_AGE_SECONDS.get_int(DEFAULT_MAX_AGE_SECONDS)
    return timedelta(seconds=max(seconds, 0))
