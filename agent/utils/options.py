"""Runtime options for open-swe.

Configured via the ``OPENSWE_OPTIONS`` env var — a comma- or whitespace-
separated list of option tokens. Unknown tokens raise at import so typos
surface immediately rather than silently falling through.

Examples::

    OPENSWE_OPTIONS=nofixedrepo
    OPENSWE_OPTIONS="nofixedrepo otherflag"

Current options:

- ``nofixedrepo``: do not pre-resolve a repository for trigger-based runs.
  The agent is given a sandbox and decides what (if anything) to clone based
  on the conversation. Suppresses the "Using repository: X" preamble and
  skips the trigger-level org allowlist; tools that need a repo still do
  (``commit_and_open_pr``, ``github_comment``).

- ``slackv2``: adopt the "Slack v2" assistant-native behaviors: ack a
  mention by calling the Assistant API (``assistant.threads.setStatus`` →
  "is thinking...") instead of adding an :eyes: reaction, and reply in a
  DM as a top-level message rather than as a thread reply. Channel threads
  are unchanged.

- ``allowanyghuser``: **LOCAL DEV ONLY.** Bypass the
  ``GITHUB_USER_EMAIL_MAP`` allowlist on the GitHub webhook — unknown
  logins get a synthetic ``{login}@local`` email instead of being silently
  dropped. Intended for fake-deps / offline testing where the operator's
  real GitHub login isn't in the committed employee map. Do not ship
  with this enabled.
"""

from __future__ import annotations

import os
import re

_RAW = os.environ.get("OPENSWE_OPTIONS", "")
OPTIONS: frozenset[str] = frozenset(t for t in re.split(r"[,\s]+", _RAW) if t)

KNOWN_OPTIONS: frozenset[str] = frozenset({"nofixedrepo", "slackv2", "allowanyghuser"})

_unknown = OPTIONS - KNOWN_OPTIONS
if _unknown:
    raise ValueError(
        f"Unknown option(s) in OPENSWE_OPTIONS: {sorted(_unknown)}. "
        f"Known: {sorted(KNOWN_OPTIONS)}"
    )


def is_option_enabled(name: str) -> bool:
    return name in OPTIONS


def nofixedrepo_enabled() -> bool:
    return "nofixedrepo" in OPTIONS


def slackv2_enabled() -> bool:
    return "slackv2" in OPTIONS


def allow_any_gh_user_enabled() -> bool:
    return "allowanyghuser" in OPTIONS
