"""Sign in to a local dashboard with the `gh` CLI's credentials.

A machine-local GitHub App is otherwise required just to look at real data: the
login flow wants ``GITHUB_APP_CLIENT_ID`` and the browser leg wants a registered
callback. The dashboard's per-user reads — the PR list, one PR's details, a PR
preview — already run on the signed-in person's own OAuth token, and `gh` holds
one, so the App buys nothing for them.

This is local-development only, and refuses to run anywhere else: `langgraph dev`
is the only runtime that reports the ``local_dev`` API variant. Endpoints backed by
the App (a published review and its diff) still need one.
"""

import asyncio
import json
import logging

from pydantic import BaseModel, ValidationError

from agent.config import ENV

logger = logging.getLogger(__name__)

_GH_TIMEOUT_SECONDS = 30


class GhUnavailable(RuntimeError):
    """`gh` is missing, not logged in, or answered with something unusable."""


class GhCredentials(BaseModel):
    external_id: str
    login: str
    email: str
    display_name: str
    avatar_url: str
    token: str


class _GhViewer(BaseModel):
    id: int | None = None
    login: str
    email: str | None = None
    name: str | None = None
    avatar_url: str | None = None


def dev_login_enabled() -> bool:
    """True only under `langgraph dev`, which is the sole local-dev runtime."""
    return ENV.LANGSMITH_LANGGRAPH_API_VARIANT.get() == "local_dev"


async def _gh(*args: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "gh", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise GhUnavailable("the `gh` CLI is not installed") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_GH_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        raise GhUnavailable(f"`gh {' '.join(args)}` timed out") from exc
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or "no output"
        raise GhUnavailable(f"`gh {' '.join(args)}` failed: {detail}")
    return stdout.decode(errors="replace").strip()


async def gh_credentials() -> GhCredentials:
    """The `gh` CLI's token and the person it belongs to."""
    token, viewer_json = await asyncio.gather(_gh("auth", "token"), _gh("api", "user"))
    if not token:
        raise GhUnavailable("`gh auth token` returned nothing — run `gh auth login`")
    try:
        viewer = _GhViewer.model_validate(json.loads(viewer_json))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise GhUnavailable("`gh api user` did not describe a GitHub user") from exc
    return GhCredentials(
        external_id=str(viewer.id) if viewer.id is not None else viewer.login,
        login=viewer.login,
        email=viewer.email or "",
        display_name=viewer.name or "",
        avatar_url=viewer.avatar_url or "",
        token=token,
    )
