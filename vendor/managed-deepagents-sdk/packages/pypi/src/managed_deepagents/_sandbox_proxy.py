"""Resolve sandbox proxy templates only at command execution."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import json
import re
from collections.abc import Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from langchain_core.runnables.config import var_child_runnable_config
from langgraph.errors import GraphInterrupt

from managed_deepagents._agent_auth_http import CredentialStoreUnavailableError
from managed_deepagents._connections import (
    ConnectionNotFoundError,
    ConnectionReference,
    CredentialAuthorizationRequiredError,
    is_connection_reference,
)
from managed_deepagents._langsmith_env import resolve_langsmith_endpoint
from managed_deepagents._run_local import RunLocal
from managed_deepagents.sandbox import ConnectionHeader, SandboxDefinition

if TYPE_CHECKING:
    import httpx
    from langsmith.sandbox import SandboxProxyConfig

    from managed_deepagents._credential_gate import CredentialConsumer


def _rules(config: object) -> list[dict[str, object]]:
    if not isinstance(config, dict) or not isinstance(config.get("rules"), list):
        return []
    return cast("list[dict[str, object]]", config["rules"])


def _headers(rule: Mapping[str, object]) -> list[dict[str, object]]:
    headers = rule.get("headers")
    return cast("list[dict[str, object]]", headers) if isinstance(headers, list) else []


def _reference(value: object) -> ConnectionReference | None:
    if is_connection_reference(value):
        return value
    return value.connection if isinstance(value, ConnectionHeader) else None


def validate_and_collect_sandbox_proxy_config_references(
    config: object,
) -> tuple[ConnectionReference, ...]:
    references = []
    for rule in _rules(config):
        for header in _headers(rule):
            reference = _reference(header.get("value"))
            if reference is not None:
                if "type" in header:
                    raise TypeError("connection-backed sandbox headers cannot set type")
                references.append(reference)
    return tuple(references)


def static_sandbox_proxy_config(config: object) -> object:
    if not validate_and_collect_sandbox_proxy_config_references(config):
        return config
    rules = []
    for rule in _rules(config):
        headers = [header for header in _headers(rule) if _reference(header.get("value")) is None]
        # The server requires a header or another action in each rule.
        if not headers and _headers(rule) and rule.get("type", "headers") == "headers":
            continue
        rules.append({**rule, "headers": headers} if "headers" in rule else dict(rule))
    return {**cast("dict[str, object]", config), "rules": rules}


def _format_header(value: object, resolved_token: str) -> str:
    if not isinstance(value, ConnectionHeader):
        return resolved_token
    if value.format == "bearer":
        return f"Bearer {resolved_token}"
    if value.format == "template":
        return cast("str", value.template).replace("{token}", resolved_token)
    encoded = base64.b64encode(f"{value.username}:{resolved_token}".encode()).decode()
    return f"Basic {encoded}"


async def resolve_sandbox_proxy_config(
    config: object, run_config: Mapping[str, object] | None
) -> SandboxProxyConfig:
    if not validate_and_collect_sandbox_proxy_config_references(config):
        return cast("SandboxProxyConfig", config)
    # Preserve graph interrupt state while supplying a backend's bound caller.
    config_context = var_child_runnable_config
    current_config = config_context.get() or {}
    # Existing ambient credentials and interrupt state must stay authoritative.
    effective_config = (
        current_config
        if current_config.get("configurable")
        else {**current_config, **(run_config or {})}
    )
    context_reset_token = config_context.set(effective_config)
    try:
        tokens_by_connection: dict[tuple[str, str, str | None], str] = {}
        rules = []
        for rule in _rules(config):
            headers = []
            for header in _headers(rule):
                value = header.get("value")
                reference = _reference(value)
                if reference is None:
                    headers.append(dict(header))
                    continue
                connection_key = (
                    reference.slug,
                    reference.owner["type"],
                    reference.owner.get("id"),
                )
                if connection_key not in tokens_by_connection:
                    tokens_by_connection[connection_key] = await reference
                resolved_token = tokens_by_connection[connection_key]
                header_value = _format_header(value, resolved_token)
                headers.append({**header, "value": header_value, "type": "opaque"})
            rules.append({**rule, "headers": headers} if "headers" in rule else dict(rule))
        return {**cast("dict[str, object]", config), "rules": rules}
    except (
        ConnectionNotFoundError,
        CredentialAuthorizationRequiredError,
        CredentialStoreUnavailableError,
    ):
        raise
    except Exception as error:
        if isinstance(error, GraphInterrupt):
            raise
        raise RuntimeError("Could not resolve sandbox proxy connections.") from None
    finally:
        config_context.reset(context_reset_token)


def run_sync(coroutine: Coroutine[object, object, None]) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coroutine)
        return
    context = contextvars.copy_context()

    def run() -> None:
        asyncio.run(coroutine)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(context.run, run).result()


def patch_sandbox_proxy_config(
    sandbox: SandboxDefinition, name: str, config: SandboxProxyConfig
) -> None:
    from langsmith.sandbox import ResourceNotFoundError  # noqa: PLC0415

    from managed_deepagents.runtime import (  # noqa: PLC0415
        _configured_sandbox_api_endpoint,
        _sandbox_client_kwargs,
    )

    kwargs = _sandbox_client_kwargs(sandbox)
    headers = dict(cast("Mapping[str, str]", kwargs.get("headers") or {}))
    api_key = kwargs.get("api_key")
    if isinstance(api_key, str):
        headers["X-Api-Key"] = api_key
    endpoint = _configured_sandbox_api_endpoint()
    if endpoint is None:
        endpoint = f"{resolve_langsmith_endpoint().rstrip('/')}/v2/sandboxes"
    httpx = import_module("httpx")
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.patch(
                f"{endpoint}/boxes/{quote(name, safe='')}",
                headers=headers,
                json={"proxy_config": config},
            )
    except Exception:  # noqa: BLE001 — omit secret-bearing transport causes
        raise RuntimeError("Could not update sandbox proxy credentials.") from None
    if response.status_code == HTTPStatus.NOT_FOUND:
        raise ResourceNotFoundError(
            "Could not update sandbox proxy credentials.", "sandbox"
        ) from None
    if not response.is_success:
        raise ProxyConfigRefusedError.of(response, config)


#: Credential-shaped text an API error may echo back, never repeated in an error message.
_SECRET_TEXT = re.compile(
    r"(gh[opsu]_|github_pat_|lsv2_[a-z]{2}_|Bearer\s+|Basic\s+)[A-Za-z0-9_.=+/-]+", re.IGNORECASE
)
_DETAIL_LIMIT = 300
#: Shorter pieces of a sent value (a scheme such as ``Bearer``) are not credentials.
_SECRET_PIECE_MIN = 8


def _sent_secrets(config: object) -> list[str]:
    """Each credential ``config`` carries, as an API error could echo it, longest first.

    That is every ``value`` the config sends (header values, opaque secrets), whole and
    split on whitespace (a ``Bearer`` or ``Basic`` header's token), plain and JSON-escaped.
    Workspace secret references name a secret rather than carry one.
    """
    found: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, Mapping):
            value = node.get("value")
            if isinstance(value, str) and node.get("type") != "workspace_secret":
                whole = " ".join(value.split())
                for piece in (whole, *whole.split(" ")):
                    if len(piece) >= _SECRET_PIECE_MIN:
                        found.update((piece, json.dumps(piece)[1:-1]))
            for child in node.values():
                visit(child)
        elif isinstance(node, list | tuple):
            for child in node:
                visit(child)

    visit(config)
    return sorted(found, key=len, reverse=True)


class ProxyConfigRefusedError(RuntimeError):
    """The sandbox API answered a proxy config write with an error; its message says why,
    credentials removed.

    Without it a refusal (for example HTTP 422) does not say which part of the config the API
    rejected.
    """

    @classmethod
    def of(cls, response: httpx.Response, sent: object = None) -> ProxyConfigRefusedError:
        """The error for ``response``: its status, and the API's ``detail`` where it has one.

        ``sent`` is the config that was written: any of its values the detail echoes is
        removed, whatever its format.
        """
        status, detail = response.status_code, cls._detail(response, sent)
        return cls(f"Could not update sandbox proxy credentials (HTTP {status}{detail}).")

    @staticmethod
    def _detail(response: httpx.Response, sent: object) -> str:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 — a body that is not JSON says nothing safe to repeat
            return ""
        detail = body.get("detail") if isinstance(body, Mapping) else None
        if detail is None:
            return ""
        text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        text = " ".join(text.split())
        for secret in _sent_secrets(sent):
            text = text.replace(secret, "[redacted]")
        text = _SECRET_TEXT.sub(r"\1[redacted]", text)
        if len(text) > _DETAIL_LIMIT:
            text = f"{text[: _DETAIL_LIMIT - 1]}…"
        return f": {text}"


class SandboxProxyConfigSync:
    """Remember which proxy config each run already applied to each sandbox.

    The memo is per run: a cached graph serves many runs with one instance, and the
    shared sandbox's config can be changed by other runs, replicas or graphs in the
    meantime. So each run applies its own config once before its first command,
    then skips re-applying the same digest for the rest of the run.
    """

    def __init__(self) -> None:
        self._runs: RunLocal[dict[str, str]] = RunLocal(dict)

    @property
    def _hashes(self) -> dict[str, str]:
        return self._runs.get()

    async def sync(
        self,
        sandbox: SandboxDefinition,
        name: str | None,
        config: Mapping[str, object] | None,
        *,
        keeps_proxy: bool = False,
    ) -> None:
        """Write this run's proxy config on sandbox ``name`` before its first command.

        An existing sandbox, named by the run or opened from its lead's binding
        (``keeps_proxy``), keeps its own proxy config unless the run declares one.
        """
        from langsmith.sandbox import ResourceNotFoundError  # noqa: PLC0415

        template = sandbox.options.get("proxy_config")
        if template is None and (keeps_proxy or sandbox.options.get("existing_sandbox")):
            return
        if not name:
            raise RuntimeError("Cannot update sandbox proxy credentials without a sandbox name.")
        # Proxy state belongs to the shared Host box. Set this run's config,
        # including empty rules, before executing commands.
        wire = (
            await resolve_sandbox_proxy_config(template, config)
            if template is not None
            else {"rules": []}
        )
        digest = hashlib.sha256(json.dumps(wire, sort_keys=True).encode()).hexdigest()
        if self._hashes.get(name) == digest:
            return
        try:
            await asyncio.to_thread(patch_sandbox_proxy_config, sandbox, name, wire)
        except Exception as error:  # noqa: BLE001 — omit secret-bearing transport causes
            if isinstance(error, ResourceNotFoundError):
                raise ResourceNotFoundError(
                    "Could not update sandbox proxy credentials.", "sandbox"
                ) from None
            if isinstance(error, ProxyConfigRefusedError):
                raise ProxyConfigRefusedError(str(error)) from None
            raise RuntimeError("Could not update sandbox proxy credentials.") from None
        self._hashes[name] = digest


def sandbox_has_proxy_connections(sandbox: SandboxDefinition | None) -> bool:
    """Whether the sandbox proxy config resolves any connection through Agent Auth."""
    return sandbox is not None and bool(
        validate_and_collect_sandbox_proxy_config_references(sandbox.options.get("proxy_config"))
    )


def sandbox_credential_consumers(sandbox: SandboxDefinition | None) -> list[CredentialConsumer]:
    from managed_deepagents._credential_gate import CredentialConsumer  # noqa: PLC0415

    if sandbox is None:
        return []
    references = tuple(
        ref
        for ref in validate_and_collect_sandbox_proxy_config_references(
            sandbox.options.get("proxy_config")
        )
        if ref.owner["type"] == "user"
    )
    return (
        [CredentialConsumer(sandbox, references, lambda _tokens, _context: [])]
        if references
        else []
    )
