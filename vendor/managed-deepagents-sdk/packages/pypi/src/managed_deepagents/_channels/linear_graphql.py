"""Linear's GraphQL API as the deployment's app."""

from __future__ import annotations

import os
from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, NotRequired, TypeVar, cast
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict

from managed_deepagents._channels.http.provider import CachedRead
from managed_deepagents._channels.linear_connections import LINEAR_TOKEN_SLUG
from managed_deepagents._channels.scalars import (
    Nonblank,  # noqa: TC001 - pydantic reads it at runtime
)

if TYPE_CHECKING:
    from managed_deepagents._channels.http.types import HttpPostedMessage

_LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
_LINEAR_API_TIMEOUT_SECONDS = 10.0
_LINEAR_RESPONSE_MAX_BYTES = 1_000_000
_TOKEN_TTL_SECONDS = 300.0
"""How long the app token read from Agent Auth is reused."""
_APP_USER_TTL_SECONDS = 3600.0
"""How long the app's user is reused. It changes only when the app is installed
anew, which a long-lived process should notice too."""

_T = TypeVar("_T")


def _graphql_url() -> str:
    """Where GraphQL requests go.

    Linear's API, or under ``mda dev`` (``MDA_LOCAL_DEV=1``) the stand-in
    ``MDA_LINEAR_API_URL`` names, so local tests never reach Linear. A
    deployment always uses Linear's API.
    """
    override = os.environ.get("MDA_LINEAR_API_URL", "").strip()
    if os.environ.get("MDA_LOCAL_DEV") != "1" or not override:
        return _LINEAR_GRAPHQL_URL
    if urlsplit(override).scheme not in ("http", "https"):
        raise ValueError("MDA_LINEAR_API_URL must be an http(s) URL")
    return override


class LinearRateLimitError(RuntimeError):
    """Linear refused a request for exceeding its rate limit."""

    def __init__(self) -> None:
        super().__init__("Linear rate limit exceeded")


async def _read_token() -> str:
    from managed_deepagents._connections import connections  # noqa: PLC0415

    return await connections.resolve_deployment_secret(LINEAR_TOKEN_SLUG)


_app_token = CachedRead(_read_token, lambda _: _TOKEN_TTL_SECONDS)
"""The app's access token, read from Agent Auth at most every few minutes."""


def reset_linear_state() -> None:
    """Forget the cached app token and app user; for tests."""
    _app_token.reset()
    _app_user.reset()


def _body(response: httpx.Response) -> Mapping[str, object]:
    if len(response.content) > _LINEAR_RESPONSE_MAX_BYTES:
        return {}
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return cast("Mapping[str, object]", parsed) if isinstance(parsed, Mapping) else {}


def _error_codes(body: Mapping[str, object]) -> list[str]:
    errors = body.get("errors")
    codes: list[str] = []
    for error in errors if isinstance(errors, list) else []:
        extensions = error.get("extensions") if isinstance(error, Mapping) else None
        code = extensions.get("code") if isinstance(extensions, Mapping) else None
        if isinstance(code, str):
            codes.append(code)
    return codes


async def linear_request(
    query: str, variables: Mapping[str, object], schema: TypeAdapter[_T]
) -> _T:
    """Run a GraphQL operation as the app and return the response's ``data``.

    ``query`` is a constant; everything that varies goes in ``variables``. A
    rejected token is read again once. ``data`` is checked against ``schema``.
    """
    async with httpx.AsyncClient(
        timeout=_LINEAR_API_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:

        async def send() -> httpx.Response:
            return await client.post(
                _graphql_url(),
                headers={"Authorization": f"Bearer {await _app_token.get()}"},
                json={"query": query, "variables": dict(variables)},
            )

        response = await send()
        body = _body(response)
        if (
            response.status_code == HTTPStatus.UNAUTHORIZED
            or "AUTHENTICATION_ERROR" in _error_codes(body)
        ):
            _app_token.reset()
            response = await send()
            body = _body(response)
    if "RATELIMITED" in _error_codes(body):
        raise LinearRateLimitError
    if not response.is_success or body.get("errors") or "data" not in body:
        raise RuntimeError(f"Linear refused the request (HTTP {response.status_code})")
    try:
        return schema.validate_python(body["data"])
    except ValidationError:
        raise RuntimeError("Linear answered in an unexpected shape") from None


@with_config(ConfigDict(extra="allow", strict=True))
class _ViewerUser(TypedDict):
    id: Nonblank
    displayName: str


@with_config(ConfigDict(extra="allow", strict=True))
class _Viewer(TypedDict):
    viewer: _ViewerUser


_VIEWER_QUERY = "query MdaLinearViewer { viewer { id displayName } }"
_viewer_adapter = TypeAdapter(_Viewer)


class LinearAppUser(TypedDict):
    """The Linear user the app acts as."""

    id: str
    display_name: str


async def _read_app_user() -> LinearAppUser:
    viewer = (await linear_request(_VIEWER_QUERY, {}, _viewer_adapter))["viewer"]
    return {"id": viewer["id"], "display_name": viewer["displayName"]}


_app_user = CachedRead(_read_app_user, lambda _: _APP_USER_TTL_SECONDS)


async def linear_app_user() -> LinearAppUser:
    """The Linear user the app acts as (``id``, ``display_name``), read at most hourly.

    Its own comments and changes are ignored, and a comment mentioning its
    ``display_name`` starts an agent session.
    """
    return await _app_user.get()


_COMMENT_CREATE_MUTATION = """mutation MdaLinearCommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) { success comment { id url } }
}"""


@with_config(ConfigDict(extra="allow", strict=True))
class _CreatedComment(TypedDict):
    id: Nonblank
    url: NotRequired[str | None]


@with_config(ConfigDict(extra="allow", strict=True))
class _CommentCreate(TypedDict):
    success: Literal[True]
    comment: _CreatedComment


@with_config(ConfigDict(extra="allow", strict=True))
class _CommentCreated(TypedDict):
    commentCreate: _CommentCreate


_comment_create_adapter = TypeAdapter(_CommentCreated)


async def create_linear_comment(
    *, issue_id: str, body: str, parent_id: str | None = None
) -> HttpPostedMessage:
    """Comment on an issue as the app, threaded under ``parent_id`` when given.

    Returns the comment's ``id`` and ``url``.
    """
    comment_input: dict[str, object] = {"issueId": issue_id, "body": body}
    if parent_id is not None:
        comment_input["parentId"] = parent_id
    data = await linear_request(
        _COMMENT_CREATE_MUTATION, {"input": comment_input}, _comment_create_adapter
    )
    comment = data["commentCreate"]["comment"]
    posted: HttpPostedMessage = {"id": comment["id"]}
    url = comment.get("url")
    if url:
        posted["url"] = url
    return posted


_COMMENT_SESSION_QUERY = """query MdaLinearCommentSession($id: String!) {
  comment(id: $id) { agentSession { id } }
}"""


@with_config(ConfigDict(extra="allow", strict=True))
class _SessionRef(TypedDict):
    id: Nonblank


@with_config(ConfigDict(extra="allow", strict=True))
class _SessionComment(TypedDict):
    agentSession: _SessionRef | None


@with_config(ConfigDict(extra="allow", strict=True))
class _CommentSession(TypedDict):
    comment: _SessionComment | None


_comment_session_adapter = TypeAdapter(_CommentSession)


async def comment_in_agent_session(comment_id: str) -> bool:
    """Whether a comment belongs to an agent session's thread."""
    data = await linear_request(
        _COMMENT_SESSION_QUERY, {"id": comment_id}, _comment_session_adapter
    )
    comment = data["comment"]
    return comment is not None and comment["agentSession"] is not None


_ACTIVITY_CREATE_MUTATION = """mutation MdaLinearActivityCreate($input: AgentActivityCreateInput!) {
  agentActivityCreate(input: $input) { success agentActivity { id } }
}"""


class OutgoingActivity(TypedDict):
    """What the app says in an agent session."""

    type: Literal["thought", "response", "error", "elicitation"]
    body: str


@with_config(ConfigDict(extra="allow", strict=True))
class _ActivityCreate(TypedDict):
    success: Literal[True]
    agentActivity: _SessionRef


@with_config(ConfigDict(extra="allow", strict=True))
class _ActivityCreated(TypedDict):
    agentActivityCreate: _ActivityCreate


_activity_create_adapter = TypeAdapter(_ActivityCreated)


async def create_linear_activity(
    *,
    agent_session_id: str,
    content: OutgoingActivity,
    ephemeral: bool | None = None,
) -> HttpPostedMessage:
    """Post an activity to an agent session as the app.

    An ``ephemeral`` thought is replaced by the next activity.
    """
    activity_input: dict[str, object] = {
        "agentSessionId": agent_session_id,
        "content": dict(content),
    }
    if ephemeral is not None:
        activity_input["ephemeral"] = ephemeral
    data = await linear_request(
        _ACTIVITY_CREATE_MUTATION, {"input": activity_input}, _activity_create_adapter
    )
    return {"id": data["agentActivityCreate"]["agentActivity"]["id"]}


_SESSION_UPDATE_MUTATION = """mutation MdaLinearSessionUpdate(
  $id: String!
  $input: AgentSessionUpdateInput!
) {
  agentSessionUpdate(id: $id, input: $input) { success }
}"""


@with_config(ConfigDict(extra="allow", strict=True))
class _SessionUpdate(TypedDict):
    success: Literal[True]


@with_config(ConfigDict(extra="allow", strict=True))
class _SessionUpdated(TypedDict):
    agentSessionUpdate: _SessionUpdate


_session_update_adapter = TypeAdapter(_SessionUpdated)


async def add_linear_session_url(agent_session_id: str, *, label: str, url: str) -> None:
    """Link an agent session to a page about it, such as its Studio thread."""
    await linear_request(
        _SESSION_UPDATE_MUTATION,
        {"id": agent_session_id, "input": {"addedExternalUrls": [{"label": label, "url": url}]}},
        _session_update_adapter,
    )
