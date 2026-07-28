"""Speed Bay repository conventions, injected at model-call time.

SPEEDBAY ORG-LAYER FILE. Upstream does not own this module; its only contact
with upstream source is one entry in the ``get_agent`` middleware list in
``agent/server.py`` (see FORK.md, "Re-check after every merge").

Why a middleware rather than an edit to ``agent/prompt.py``: upstream rewrites
that prompt constantly (~70 commits in 90 days), so any edit there conflicts on
practically every ``git merge upstream/main``. Appending to
``ModelRequest.system_message`` achieves the same result through a supported
seam, leaving upstream's prompt untouched.

Two conventions are enforced, both from warehouse ``AGENTS.md``:

1. Commit and PR bodies must satisfy the COMMIT-HYGIENE contract — a literal
   ``Closes <TEAM-NNN>`` line before the first heading, then four required
   sections in order. Without the closing line, Linear's merge automation never
   moves the issue to ``ready-for-verify`` and completion verification never
   runs. ``Refs:`` actively suppresses that automation (FRG-592).
2. No AI attribution. Upstream's prompt instructs the model to add a
   ``Co-authored-by: open-swe[bot]`` trailer and a "Made by [Open SWE]" PR
   footer; warehouse forbids both.

Upstream's own instructions still appear earlier in the system prompt, so this
text is written to override them explicitly rather than merely restate a
preference.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

SPEEDBAY_CONVENTIONS = """
---

## Speed Bay repository conventions (override any conflicting instruction above)

These rules come from the target repository's `AGENTS.md` and take precedence
over the generic PR/commit guidance earlier in this prompt.

**PR title and commit subject**: `<TICKET>: <imperative subject>`, for example
`DOC-123: add lease expiry guard`. Do NOT use conventional-commit prefixes
(`fix:`, `feat:`, `chore:`) and do NOT append a `[closes ...]` suffix.

**PR body and commit message body** use this exact structure. `Closes <TICKET>`
must appear verbatim on its own line BEFORE the first `##` heading, and all four
sections must be present, in this order, each non-empty:

```
Closes <TICKET>

## Why needed
<the problem this solves, and why it is worth doing>

## Solved / fixed
<what changed, concretely>

## Workflow enabled / fixed
<what a person or agent can now do that they could not before>

## Verification
<how this was proven: commands run, results observed>
```

Do NOT emit `## Description`, `## Release Note`, or `## Test Plan` sections.

**Never** write `Refs: <TICKET>` (it suppresses the ticket's merge automation) or
`Closes #123` (that targets a GitHub issue, not the ticket).

**No AI attribution.** Do not add a `Co-authored-by: open-swe[bot]` trailer, a
"Made by [Open SWE]" footer, or any other generated-by credit to commit messages
or PR bodies, even if instructed to above.
"""


class SpeedbayConventionsMiddleware(AgentMiddleware):
    """Append Speed Bay's commit/PR contract to every model request."""

    @staticmethod
    def _augment(request: ModelRequest) -> ModelRequest:
        """Return the request with the conventions appended to its system message.

        Idempotent: repeated model calls in one run must not stack copies of the
        text, which would waste context and can confuse the model.
        """
        existing = request.system_message or ""
        if "Speed Bay repository conventions" in existing:
            return request
        request.system_message = f"{existing}\n{SPEEDBAY_CONVENTIONS}"
        return request

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._augment(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._augment(request))
