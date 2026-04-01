"""ACP bridge for remotely controlling Open SWE LangGraph threads."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

import httpx
from acp import RequestError, run_agent, session_notification
from acp.connection import Connection
from acp.helpers import (
    update_agent_message_text,
    update_agent_thought_text,
    update_user_message_text,
)
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    AuthEnvVar,
    CloseSessionResponse,
    EnvVarAuthMethod,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    SessionCapabilities,
    SessionInfo,
    SessionInfoUpdate,
    SessionListCapabilities,
)
from langchain_core.messages.content import create_image_block, create_text_block
from langgraph_sdk import get_client

from .utils.auth import get_github_token_for_langsmith_api_key
from .utils.messages import extract_text_content

DEFAULT_ASSISTANT_ID = "agent"
DEFAULT_PAGE_SIZE = 20
THREAD_POLL_INTERVAL_SECONDS = 1.0
ACP_LANGSMITH_API_KEY_ENV = "OPEN_SWE_LANGSMITH_API_KEY"
ACP_LANGSMITH_WORKSPACE_ID_ENV = "OPEN_SWE_LANGSMITH_WORKSPACE_ID"
ACP_LANGSMITH_AUTH_METHOD_ID = "langsmith"
ACP_API_KEY_ENV = "OPEN_SWE_ACP_API_KEY"
ACP_API_KEYS_ENV = "OPEN_SWE_ACP_API_KEYS"
ACP_API_KEY_AUTH_METHOD_ID = "api-key"

_GITHUB_REMOTE_RE = re.compile(
    r"^(?:git@|ssh://git@|https://|http://)?github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


class LangGraphClientFactory(Protocol):
    def __call__(self, *, url: str, api_key: str | None) -> Any: ...


@dataclass(slots=True)
class SessionState:
    session_id: str
    cwd: str
    repo_config: dict[str, str]
    title: str
    last_message_count: int = 0


@dataclass(slots=True, frozen=True)
class AuthenticatedPrincipal:
    provider: str
    subject: str
    display_name: str
    user_email: str | None
    github_token: str | None
    github_login: str | None
    github_user_id: int | None
    github_name: str | None
    allow_github_app_fallback: bool = False


@dataclass(slots=True, frozen=True)
class ConfiguredApiKey:
    key: str
    subject: str
    display_name: str
    user_email: str | None = None
    github_token: str | None = None
    github_login: str | None = None
    github_user_id: int | None = None
    github_name: str | None = None
    allow_github_app_fallback: bool = True


def _project_version() -> str:
    try:
        return version("open-swe-agent")
    except PackageNotFoundError:
        return "0.1.0"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _parse_repo_override(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    if "/" not in value:
        return None
    owner, name = value.split("/", 1)
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        return None
    return {"owner": owner, "name": name}


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _resolve_github_email(client: httpx.AsyncClient, token: str) -> str | None:
    try:
        response = await client.get("https://api.github.com/user/emails", headers=_github_headers(token))
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    emails = response.json()
    if not isinstance(emails, list):
        return None

    for email in emails:
        if (
            isinstance(email, dict)
            and email.get("primary") is True
            and email.get("verified") is True
            and isinstance(email.get("email"), str)
            and email["email"]
        ):
            return email["email"]
    for email in emails:
        if (
            isinstance(email, dict)
            and email.get("verified") is True
            and isinstance(email.get("email"), str)
            and email["email"]
        ):
            return email["email"]
    for email in emails:
        if isinstance(email, dict) and isinstance(email.get("email"), str) and email["email"]:
            return email["email"]
    return None


async def resolve_authenticated_principal_from_github_token(
    token: str,
    *,
    token_label: str,
) -> AuthenticatedPrincipal:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get("https://api.github.com/user", headers=_github_headers(token))
        except httpx.HTTPError as exc:
            raise RequestError.auth_required({"message": f"Failed to validate {token_label}: {exc}"}) from exc

    if response.status_code != 200:
        raise RequestError.auth_required(
            {
                "message": f"Failed to validate {token_label}",
                "status_code": response.status_code,
            }
        )

    payload = response.json()
    github_login = payload.get("login")
    if not isinstance(github_login, str) or not github_login:
        raise RequestError.auth_required({"message": f"{token_label} did not resolve to a GitHub user login"})

    github_type = payload.get("type")
    if isinstance(github_type, str) and github_type.lower() != "user":
        raise RequestError.auth_required(
            {
                "message": (
                    f"{token_label} must belong to a human GitHub user, "
                    f"got account type {github_type!r}"
                )
            }
        )

    user_email = payload.get("email")
    if not isinstance(user_email, str) or not user_email:
        async with httpx.AsyncClient(timeout=10.0) as client:
            user_email = await _resolve_github_email(client, token)

    github_user_id = payload.get("id") if isinstance(payload.get("id"), int) else None
    github_name = payload.get("name") if isinstance(payload.get("name"), str) and payload.get("name") else github_login
    return AuthenticatedPrincipal(
        provider="github",
        subject=github_login,
        display_name=github_name,
        user_email=user_email,
        github_token=token,
        github_login=github_login,
        github_user_id=github_user_id,
        github_name=github_name,
    )


def _hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _parse_api_key_entry(subject: str, raw_entry: Any) -> ConfiguredApiKey | None:
    if isinstance(raw_entry, str) and raw_entry:
        return ConfiguredApiKey(key=raw_entry, subject=subject, display_name=subject)
    if not isinstance(raw_entry, dict):
        return None

    key = raw_entry.get("key")
    if not isinstance(key, str) or not key:
        return None

    display_name = raw_entry.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        display_name = subject

    github_user_id = raw_entry.get("github_user_id")
    if not isinstance(github_user_id, int):
        github_user_id = None

    allow_github_app_fallback = raw_entry.get("allow_github_app_fallback")
    return ConfiguredApiKey(
        key=key,
        subject=subject,
        display_name=display_name,
        user_email=raw_entry.get("user_email") if isinstance(raw_entry.get("user_email"), str) else None,
        github_token=raw_entry.get("github_token") if isinstance(raw_entry.get("github_token"), str) else None,
        github_login=raw_entry.get("github_login") if isinstance(raw_entry.get("github_login"), str) else None,
        github_user_id=github_user_id,
        github_name=raw_entry.get("github_name") if isinstance(raw_entry.get("github_name"), str) else None,
        allow_github_app_fallback=(
            allow_github_app_fallback if isinstance(allow_github_app_fallback, bool) else True
        ),
    )


def load_configured_api_keys() -> list[ConfiguredApiKey]:
    raw_config = os.getenv(ACP_API_KEYS_ENV, "").strip()
    if not raw_config:
        return []

    try:
        parsed = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise RequestError.auth_required(
            {"message": f"{ACP_API_KEYS_ENV} must be valid JSON"}
        ) from exc

    entries: list[ConfiguredApiKey] = []
    if isinstance(parsed, dict):
        for subject, raw_entry in parsed.items():
            if not isinstance(subject, str) or not subject:
                continue
            entry = _parse_api_key_entry(subject, raw_entry)
            if entry:
                entries.append(entry)
    elif isinstance(parsed, list):
        for index, raw_entry in enumerate(parsed):
            if not isinstance(raw_entry, dict):
                continue
            subject = raw_entry.get("subject")
            if not isinstance(subject, str) or not subject:
                subject = f"api-key-{index + 1}"
            entry = _parse_api_key_entry(subject, raw_entry)
            if entry:
                entries.append(entry)

    return entries


def _extract_repo_from_remote(remote_url: str) -> dict[str, str] | None:
    match = _GITHUB_REMOTE_RE.match(remote_url.strip())
    if not match:
        return None
    return {"owner": match.group("owner"), "name": match.group("name")}


def _get_git_origin_url(cwd: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", cwd, "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    remote_url = result.stdout.strip()
    return remote_url or None


def resolve_repo_config(cwd: str, repo_override: str | None = None) -> dict[str, str]:
    env_override = _parse_repo_override(repo_override) or _parse_repo_override(os.getenv("OPEN_SWE_REPO"))
    if env_override:
        return env_override

    remote_url = _get_git_origin_url(cwd)
    if remote_url:
        repo_config = _extract_repo_from_remote(remote_url)
        if repo_config:
            return repo_config

    raise RequestError.invalid_params(
        {
            "message": (
                "Could not determine the GitHub repository for this workspace. "
                "Set OPEN_SWE_REPO=owner/name or use a checkout with a GitHub origin remote."
            ),
            "cwd": cwd,
        }
    )


def _thread_repo_config(thread: dict[str, Any]) -> dict[str, str] | None:
    metadata = thread.get("metadata")
    if not isinstance(metadata, dict):
        return None
    repo = metadata.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            return {"owner": owner, "name": name}
    return None


def _thread_title(thread: dict[str, Any], repo_config: dict[str, str]) -> str:
    metadata = thread.get("metadata")
    if isinstance(metadata, dict):
        title = metadata.get("title")
        if isinstance(title, str) and title:
            return title

        linear_issue = metadata.get("linear_issue")
        if isinstance(linear_issue, dict):
            linear_title = linear_issue.get("title")
            if isinstance(linear_title, str) and linear_title:
                return linear_title

        github_issue = metadata.get("github_issue")
        if isinstance(github_issue, dict):
            github_title = github_issue.get("title")
            if isinstance(github_title, str) and github_title:
                return github_title

    return f"{repo_config['owner']}/{repo_config['name']}"


def _thread_metadata(thread: dict[str, Any]) -> dict[str, Any]:
    metadata = thread.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _thread_acp_auth(thread: dict[str, Any]) -> dict[str, str] | None:
    acp_auth = _thread_metadata(thread).get("acp_auth")
    if not isinstance(acp_auth, dict):
        return None

    normalized: dict[str, str] = {}
    for key in ("provider", "subject", "display_name", "email", "github_login", "github_id"):
        value = acp_auth.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value
    return normalized or None


def _thread_acp_user(thread: dict[str, Any]) -> dict[str, str] | None:
    acp_user = _thread_metadata(thread).get("acp_user")
    if not isinstance(acp_user, dict):
        return None

    normalized: dict[str, str] = {}
    for key in ("login", "id", "email", "name"):
        value = acp_user.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value
    return normalized or None


def _acp_auth_metadata(principal: AuthenticatedPrincipal) -> dict[str, str]:
    metadata = {
        "provider": principal.provider,
        "subject": principal.subject,
        "display_name": principal.display_name,
    }
    if principal.user_email:
        metadata["email"] = principal.user_email
    if principal.github_login:
        metadata["github_login"] = principal.github_login
    if principal.github_user_id is not None:
        metadata["github_id"] = str(principal.github_user_id)
    return metadata


def _acp_user_metadata(principal: AuthenticatedPrincipal) -> dict[str, str] | None:
    if not principal.github_login:
        return None

    metadata = {
        "login": principal.github_login,
        "name": principal.github_name or principal.display_name,
    }
    if principal.github_user_id is not None:
        metadata["id"] = str(principal.github_user_id)
    if principal.user_email:
        metadata["email"] = principal.user_email
    return metadata


def _principal_matches_thread(thread: dict[str, Any], principal: AuthenticatedPrincipal) -> bool:
    thread_auth = _thread_acp_auth(thread)
    if thread_auth:
        return (
            thread_auth.get("provider") == principal.provider
            and thread_auth.get("subject") == principal.subject
        )

    thread_user = _thread_acp_user(thread)
    if not thread_user:
        return True

    if principal.github_user_id is not None and thread_user.get("id"):
        return thread_user["id"] == str(principal.github_user_id)
    if principal.github_login and thread_user.get("login"):
        return thread_user["login"] == principal.github_login
    if principal.user_email and thread_user.get("email"):
        return thread_user["email"].casefold() == principal.user_email.casefold()
    return False


def _message_role(message: dict[str, Any]) -> str:
    role = message.get("type") or message.get("role") or ""
    return str(role).lower()


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    return extract_text_content(content) if isinstance(content, list | str) else ""


def _first_text_block(prompt: list[Any]) -> str:
    texts: list[str] = []
    for block in prompt:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "").strip()
            if text:
                texts.append(text)
    return "\n\n".join(texts).strip()


class OpenSWEAcpAgent:
    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        assistant_id: str = DEFAULT_ASSISTANT_ID,
        repo_override: str | None = None,
        client_factory: LangGraphClientFactory | None = None,
    ) -> None:
        self.url = url or os.getenv("OPEN_SWE_LANGGRAPH_URL") or os.getenv("LANGGRAPH_URL")
        self.api_key = api_key or os.getenv("OPEN_SWE_LANGGRAPH_API_KEY") or os.getenv("LANGGRAPH_API_KEY")
        self.assistant_id = assistant_id
        self.repo_override = repo_override
        self._client_factory = client_factory or (lambda *, url, api_key: get_client(url=url, api_key=api_key))
        self._conn: Connection | None = None
        self._sessions: dict[str, SessionState] = {}
        self._active_runs: dict[str, str] = {}
        self._attach_tasks: dict[str, asyncio.Task[None]] = {}
        self._authenticated_principal: AuthenticatedPrincipal | None = None
        self._selected_auth_method_id: str | None = None

    def on_connect(self, conn: Connection) -> None:
        self._conn = conn

    async def initialize(self, **_: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(
                name="open-swe-acp",
                title="Open SWE ACP",
                version=_project_version(),
            ),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(embedded_context=True, image=True),
                session_capabilities=SessionCapabilities(list=SessionListCapabilities()),
            ),
            auth_methods=[
                EnvVarAuthMethod(
                    type="env_var",
                    id=ACP_LANGSMITH_AUTH_METHOD_ID,
                    name="LangSmith",
                    description=(
                        "Provide a LangSmith API key so Open SWE can authenticate the ACP client "
                        "through LangSmith and run as that user's GitHub identity."
                    ),
                    vars=[
                        AuthEnvVar(
                            name=ACP_LANGSMITH_API_KEY_ENV,
                            label="LangSmith API key",
                        )
                    ],
                ),
                EnvVarAuthMethod(
                    type="env_var",
                    id=ACP_API_KEY_AUTH_METHOD_ID,
                    name="Open SWE API key",
                    description=(
                        "Provide an ACP API key issued by the Open SWE deployment. "
                        "These keys can map to a specific user identity or a shared service identity."
                    ),
                    vars=[
                        AuthEnvVar(
                            name=ACP_API_KEY_ENV,
                            label="Open SWE API key",
                        )
                    ],
                )
            ],
        )

    async def authenticate(self, method_id: str, **_: Any) -> AuthenticateResponse | None:
        if method_id not in {ACP_LANGSMITH_AUTH_METHOD_ID, ACP_API_KEY_AUTH_METHOD_ID}:
            raise RequestError.invalid_params(
                {
                    "message": f"Unsupported authentication method {method_id!r}",
                    "method_id": method_id,
                }
            )
        self._selected_auth_method_id = method_id
        await self._require_authenticated_principal(force_refresh=True)
        return AuthenticateResponse()

    async def new_session(self, cwd: str, **_: Any) -> NewSessionResponse:
        principal = await self._require_authenticated_principal()
        client = self._client()
        repo_config = resolve_repo_config(cwd, repo_override=self.repo_override)
        metadata = {
            "repo": repo_config,
            "cwd": cwd,
            "title": f"{repo_config['owner']}/{repo_config['name']}",
            "source": "acp",
            "acp_auth": _acp_auth_metadata(principal),
        }
        acp_user = _acp_user_metadata(principal)
        if acp_user:
            metadata["acp_user"] = acp_user
        thread = await client.threads.create(
            metadata=metadata
        )
        session_id = thread["thread_id"]
        self._sessions[session_id] = SessionState(
            session_id=session_id,
            cwd=cwd,
            repo_config=repo_config,
            title=f"{repo_config['owner']}/{repo_config['name']}",
        )
        await self._notify_session_info(session_id)
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, cwd: str, session_id: str, **_: Any) -> LoadSessionResponse | None:
        principal = await self._require_authenticated_principal()
        client = self._client()
        thread = await client.threads.get(session_id)
        self._assert_thread_access(thread, session_id, principal)
        repo_config = _thread_repo_config(thread) or resolve_repo_config(cwd, repo_override=self.repo_override)
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            repo_config=repo_config,
            title=_thread_title(thread, repo_config),
        )
        self._sessions[session_id] = session
        await self._notify_session_info(session_id)
        await self._emit_all_messages(session_id)
        if thread.get("status") == "busy":
            await self._notify_update(
                session_id,
                update_agent_thought_text("Attached to an active Open SWE run. Waiting for new output."),
            )
            self._start_attach_task(session_id)
        return LoadSessionResponse()

    async def resume_session(self, cwd: str, session_id: str, **kwargs: Any) -> LoadSessionResponse | None:
        return await self.load_session(cwd=cwd, session_id=session_id, **kwargs)

    async def list_sessions(self, cursor: str | None = None, cwd: str | None = None, **_: Any) -> ListSessionsResponse:
        principal = await self._require_authenticated_principal()
        client = self._client()
        offset = int(cursor or "0")
        repo_filter = resolve_repo_config(cwd, repo_override=self.repo_override) if cwd else None
        threads = await client.threads.search(limit=100, offset=0, sort_by="updated_at", sort_order="desc")

        filtered_threads: list[dict[str, Any]] = []
        for thread in threads:
            if not _principal_matches_thread(thread, principal):
                continue
            repo_config = _thread_repo_config(thread)
            if repo_filter and repo_config != repo_filter:
                continue
            if not repo_config:
                continue
            filtered_threads.append(thread)

        page = filtered_threads[offset : offset + DEFAULT_PAGE_SIZE + 1]
        next_cursor = None
        if len(page) > DEFAULT_PAGE_SIZE:
            next_cursor = str(offset + DEFAULT_PAGE_SIZE)
            page = page[:DEFAULT_PAGE_SIZE]

        sessions = [
            SessionInfo(
                session_id=thread["thread_id"],
                cwd=(cwd or _thread_metadata(thread).get("cwd") or str(Path.cwd())),
                title=_thread_title(thread, _thread_repo_config(thread) or repo_filter or {"owner": "", "name": ""}),
                updated_at=thread.get("updated_at"),
            )
            for thread in page
        ]
        return ListSessionsResponse(sessions=sessions, next_cursor=next_cursor)

    async def prompt(self, prompt: list[Any], session_id: str, message_id: str | None = None, **_: Any) -> PromptResponse:
        principal = await self._require_authenticated_principal()
        client = self._client()
        session = await self._ensure_session(session_id)
        await self._cancel_attach_task(session_id)

        before_messages = await self._get_thread_messages(session_id)
        session.last_message_count = len(before_messages)

        title_hint = _first_text_block(prompt).splitlines()[0][:80].strip()
        if title_hint:
            session.title = title_hint
            await client.threads.update(
                thread_id=session_id,
                metadata=self._session_metadata(session, principal),
            )
            await self._notify_session_info(session_id)

        for block in prompt:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "").strip()
                if text:
                    await self._notify_update(session_id, update_user_message_text(text))

        content = self._convert_prompt_blocks(prompt)
        run = await client.runs.create(
            session_id,
            self.assistant_id,
            input={"messages": [{"role": "user", "content": content}]},
            config={
                "configurable": {
                    "repo": session.repo_config,
                    "source": "acp",
                    "acp_auth_provider": principal.provider,
                    "acp_auth_subject": principal.subject,
                    "github_token": principal.github_token,
                    "github_login": principal.github_login,
                    "github_user_id": principal.github_user_id,
                    "user_email": principal.user_email,
                    "allow_github_app_fallback": principal.allow_github_app_fallback,
                }
            },
            multitask_strategy="interrupt",
        )
        run_id = run["run_id"]
        self._active_runs[session_id] = run_id
        try:
            await client.runs.join(session_id, run_id)
            run_result = await client.runs.get(session_id, run_id)
        finally:
            self._active_runs.pop(session_id, None)

        if run_result["status"] == "interrupted":
            await self._emit_new_messages(session_id, skip_first_user=True)
            return PromptResponse(stop_reason="cancelled", user_message_id=message_id)

        if run_result["status"] not in {"success", "pending", "running"}:
            raise RequestError.internal_error(
                {
                    "message": "Open SWE run did not complete successfully",
                    "status": run_result["status"],
                    "run_id": run_id,
                }
            )

        await self._emit_new_messages(session_id, skip_first_user=True)
        return PromptResponse(stop_reason="end_turn", user_message_id=message_id)

    async def set_session_mode(self, **_: Any) -> None:
        return None

    async def set_session_model(self, **_: Any) -> None:
        return None

    async def set_config_option(self, **_: Any) -> None:
        return None

    async def fork_session(self, **_: Any) -> None:
        raise RequestError.method_not_found("session/fork")

    async def close_session(self, session_id: str, **_: Any) -> CloseSessionResponse | None:
        await self._cancel_attach_task(session_id)
        self._sessions.pop(session_id, None)
        self._active_runs.pop(session_id, None)
        return CloseSessionResponse()

    async def cancel(self, session_id: str, **_: Any) -> None:
        await self._cancel_attach_task(session_id)
        run_id = self._active_runs.get(session_id)
        if not run_id:
            return
        await self._client().runs.cancel(session_id, run_id, wait=False, action="interrupt")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    def _client(self) -> Any:
        if not self.url:
            raise RequestError.invalid_request(
                {
                    "message": (
                        "Open SWE ACP requires a LangGraph deployment URL. "
                        "Set OPEN_SWE_LANGGRAPH_URL or pass --url."
                    )
                }
            )
        return self._client_factory(url=self.url, api_key=self.api_key)

    async def _ensure_session(self, session_id: str) -> SessionState:
        session = self._sessions.get(session_id)
        if session:
            return session
        principal = await self._require_authenticated_principal()
        thread = await self._client().threads.get(session_id)
        self._assert_thread_access(thread, session_id, principal)
        repo_config = _thread_repo_config(thread)
        if not repo_config:
            raise RequestError.resource_not_found(session_id)
        session = SessionState(
            session_id=session_id,
            cwd=_thread_metadata(thread).get("cwd") or str(Path.cwd()),
            repo_config=repo_config,
            title=_thread_title(thread, repo_config),
        )
        self._sessions[session_id] = session
        return session

    async def _require_authenticated_principal(self, *, force_refresh: bool = False) -> AuthenticatedPrincipal:
        if self._authenticated_principal is not None and not force_refresh:
            return self._authenticated_principal

        method_id = self._selected_auth_method_id or self._detect_auth_method_id()
        if method_id == ACP_LANGSMITH_AUTH_METHOD_ID:
            principal = await self._authenticate_with_langsmith()
        elif method_id == ACP_API_KEY_AUTH_METHOD_ID:
            principal = await self._authenticate_with_api_key()
        else:
            raise RequestError.invalid_params(
                {
                    "message": f"Unsupported authentication method {method_id!r}",
                    "method_id": method_id,
                }
            )

        if (
            self._authenticated_principal is not None
            and (
                self._authenticated_principal.provider != principal.provider
                or self._authenticated_principal.subject != principal.subject
            )
        ):
            self._sessions.clear()
            self._active_runs.clear()
        self._authenticated_principal = principal
        return principal

    def _detect_auth_method_id(self) -> str:
        configured_methods = []
        if os.getenv(ACP_LANGSMITH_API_KEY_ENV, "").strip():
            configured_methods.append(ACP_LANGSMITH_AUTH_METHOD_ID)
        if os.getenv(ACP_API_KEY_ENV, "").strip():
            configured_methods.append(ACP_API_KEY_AUTH_METHOD_ID)

        if len(configured_methods) == 1:
            return configured_methods[0]
        if len(configured_methods) > 1:
            raise RequestError.auth_required(
                {
                    "message": (
                        "Multiple Open SWE ACP auth credentials are configured. "
                        "Call authenticate with the desired method."
                    )
                }
            )
        raise RequestError.auth_required(
            {
                "message": (
                    "Open SWE ACP requires authentication. "
                    f"Set {ACP_LANGSMITH_API_KEY_ENV} or {ACP_API_KEY_ENV} and call authenticate."
                )
            }
        )

    async def _authenticate_with_langsmith(self) -> AuthenticatedPrincipal:
        api_key = os.getenv(ACP_LANGSMITH_API_KEY_ENV, "").strip()
        if not api_key:
            raise RequestError.auth_required(
                {
                    "message": (
                        "Open SWE ACP requires a LangSmith API key for LangSmith auth. "
                        f"Set {ACP_LANGSMITH_API_KEY_ENV} and call authenticate."
                    ),
                    "env_var": ACP_LANGSMITH_API_KEY_ENV,
                }
            )

        workspace_id = (
            os.getenv(ACP_LANGSMITH_WORKSPACE_ID_ENV, "").strip()
            or os.getenv("LANGSMITH_WORKSPACE_ID", "").strip()
            or os.getenv("LANGSMITH_TENANT_ID_PROD", "").strip()
            or None
        )
        github_auth = await get_github_token_for_langsmith_api_key(api_key, workspace_id)
        auth_url = github_auth.get("auth_url")
        if isinstance(auth_url, str) and auth_url:
            raise RequestError.auth_required(
                {
                    "message": (
                        "This LangSmith user still needs to connect GitHub for Open SWE. "
                        f"Complete GitHub auth in LangSmith: {auth_url}"
                    )
                }
            )

        github_token = github_auth.get("token")
        if not isinstance(github_token, str) or not github_token:
            error = github_auth.get("error", "unknown error")
            raise RequestError.auth_required(
                {"message": f"Failed to authenticate with LangSmith: {error}"}
            )

        github_principal = await resolve_authenticated_principal_from_github_token(
            github_token,
            token_label=f"{ACP_LANGSMITH_API_KEY_ENV} GitHub exchange",
        )
        subject = github_principal.github_login or f"key:{_hash_api_key(api_key)}"
        return AuthenticatedPrincipal(
            provider="langsmith",
            subject=subject,
            display_name=github_principal.display_name,
            user_email=github_principal.user_email,
            github_token=github_token,
            github_login=github_principal.github_login,
            github_user_id=github_principal.github_user_id,
            github_name=github_principal.github_name,
        )

    async def _authenticate_with_api_key(self) -> AuthenticatedPrincipal:
        api_key = os.getenv(ACP_API_KEY_ENV, "").strip()
        if not api_key:
            raise RequestError.auth_required(
                {
                    "message": (
                        "Open SWE ACP requires a configured API key. "
                        f"Set {ACP_API_KEY_ENV} and call authenticate."
                    ),
                    "env_var": ACP_API_KEY_ENV,
                }
            )

        for configured_key in load_configured_api_keys():
            if not hmac.compare_digest(configured_key.key, api_key):
                continue

            if configured_key.github_token:
                github_principal = await resolve_authenticated_principal_from_github_token(
                    configured_key.github_token,
                    token_label=f"{ACP_API_KEYS_ENV} github_token",
                )
                return AuthenticatedPrincipal(
                    provider="api_key",
                    subject=configured_key.subject,
                    display_name=configured_key.display_name,
                    user_email=configured_key.user_email or github_principal.user_email,
                    github_token=configured_key.github_token,
                    github_login=configured_key.github_login or github_principal.github_login,
                    github_user_id=configured_key.github_user_id or github_principal.github_user_id,
                    github_name=configured_key.github_name or github_principal.github_name,
                    allow_github_app_fallback=configured_key.allow_github_app_fallback,
                )

            return AuthenticatedPrincipal(
                provider="api_key",
                subject=configured_key.subject,
                display_name=configured_key.display_name,
                user_email=configured_key.user_email,
                github_token=None,
                github_login=configured_key.github_login,
                github_user_id=configured_key.github_user_id,
                github_name=configured_key.github_name,
                allow_github_app_fallback=configured_key.allow_github_app_fallback,
            )

        raise RequestError.auth_required({"message": "Invalid Open SWE ACP API key"})

    def _assert_thread_access(
        self,
        thread: dict[str, Any],
        session_id: str,
        principal: AuthenticatedPrincipal,
    ) -> None:
        if _principal_matches_thread(thread, principal):
            return
        raise RequestError.auth_required(
            {
                "message": "This Open SWE thread belongs to a different ACP identity",
                "session_id": session_id,
            }
        )

    def _session_metadata(self, session: SessionState, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "repo": session.repo_config,
            "cwd": session.cwd,
            "title": session.title,
            "source": "acp",
            "acp_auth": _acp_auth_metadata(principal),
        }
        acp_user = _acp_user_metadata(principal)
        if acp_user:
            metadata["acp_user"] = acp_user
        return metadata

    def _convert_prompt_blocks(self, prompt: list[Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in prompt:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                blocks.append(create_text_block(block.text))
                continue
            if block_type == "image":
                blocks.append(
                    create_image_block(
                        base64=getattr(block, "data", None),
                        mime_type=getattr(block, "mime_type", None),
                        url=getattr(block, "uri", None),
                    )
                )
                continue
            if block_type == "resource_link":
                label = getattr(block, "title", None) or getattr(block, "name", "Resource")
                blocks.append(create_text_block(f"{label}: {getattr(block, 'uri', '')}"))
                continue
            if block_type == "resource":
                resource = getattr(block, "resource", None)
                text = getattr(resource, "text", None)
                uri = getattr(resource, "uri", None)
                if text:
                    blocks.append(create_text_block(text))
                elif uri:
                    blocks.append(create_text_block(f"Embedded resource: {uri}"))
        if not blocks:
            blocks.append(create_text_block("(empty prompt)"))
        return blocks

    async def _get_thread_messages(self, session_id: str) -> list[dict[str, Any]]:
        state = await self._client().threads.get_state(session_id)
        values = state.get("values", {})
        if not isinstance(values, dict):
            return []
        messages = values.get("messages", [])
        return messages if isinstance(messages, list) else []

    async def _emit_all_messages(self, session_id: str) -> None:
        session = await self._ensure_session(session_id)
        messages = await self._get_thread_messages(session_id)
        await self._emit_messages(session_id, messages)
        session.last_message_count = len(messages)

    async def _emit_new_messages(self, session_id: str, *, skip_first_user: bool = False) -> None:
        session = await self._ensure_session(session_id)
        messages = await self._get_thread_messages(session_id)
        start_index = session.last_message_count
        if skip_first_user and start_index < len(messages):
            next_message = messages[start_index]
            if _message_role(next_message) in {"human", "user"}:
                start_index += 1
        await self._emit_messages(session_id, messages[start_index:])
        session.last_message_count = len(messages)

    async def _emit_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            text = _message_text(message)
            if not text:
                continue
            role = _message_role(message)
            if role in {"human", "user"}:
                update = update_user_message_text(text)
            elif role in {"ai", "assistant"}:
                update = update_agent_message_text(text)
            else:
                continue
            await self._notify_update(session_id, update)

    async def _notify_session_info(self, session_id: str) -> None:
        session = await self._ensure_session(session_id)
        await self._notify_update(
            session_id,
            SessionInfoUpdate(
                session_update="session_info_update",
                title=session.title,
                updated_at=_utcnow(),
            ),
        )

    async def _notify_update(self, session_id: str, update: Any) -> None:
        if not self._conn:
            return
        await self._conn.send_notification(
            "session/update",
            session_notification(session_id, update).model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
                exclude_unset=True,
            ),
        )

    def _start_attach_task(self, session_id: str) -> None:
        self._attach_tasks[session_id] = asyncio.create_task(self._attach_when_idle(session_id))

    async def _cancel_attach_task(self, session_id: str) -> None:
        task = self._attach_tasks.pop(session_id, None)
        if not task:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _attach_when_idle(self, session_id: str) -> None:
        try:
            while True:
                thread = await self._client().threads.get(session_id)
                if thread.get("status") != "busy":
                    break
                await asyncio.sleep(THREAD_POLL_INTERVAL_SECONDS)
            await self._emit_new_messages(session_id)
        finally:
            self._attach_tasks.pop(session_id, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open SWE ACP connector")
    parser.add_argument("--url", help="LangGraph deployment URL for the remote Open SWE agent")
    parser.add_argument("--api-key", help="LangGraph API key", default=None)
    parser.add_argument("--assistant-id", default=DEFAULT_ASSISTANT_ID, help="LangGraph assistant ID")
    parser.add_argument("--repo", help="Override repo detection with owner/name", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(
        run_agent(
            OpenSWEAcpAgent(
                url=args.url,
                api_key=args.api_key,
                assistant_id=args.assistant_id,
                repo_override=args.repo,
            )
        )
    )


if __name__ == "__main__":
    main()
