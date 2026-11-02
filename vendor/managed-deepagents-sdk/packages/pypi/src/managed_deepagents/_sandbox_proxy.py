"""Resolve sandbox proxy templates only at command execution."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import json
from collections.abc import Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from langchain_core.runnables.config import var_child_runnable_config
from langgraph.errors import GraphInterrupt

from managed_deepagents._connections import (
    ConnectionNotFoundError,
    ConnectionReference,
    CredentialAuthorizationRequiredError,
    is_connection_reference,
)
from managed_deepagents._langsmith_env import resolve_langsmith_endpoint
from managed_deepagents.sandbox import ConnectionHeader, SandboxDefinition

if TYPE_CHECKING:
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
    except (ConnectionNotFoundError, CredentialAuthorizationRequiredError):
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
        raise RuntimeError("Could not update sandbox proxy credentials.")


class SandboxProxyConfigSync:
    """Keep wire-config digests for one graph factory invocation.

    MDA rebuilds the graph for every run and resume, so each starts with an empty cache.
    """

    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}

    def invalidate(self, name: str | None) -> None:
        if name is not None:
            self._hashes.pop(name, None)

    async def sync(
        self,
        sandbox: SandboxDefinition,
        name: str | None,
        config: Mapping[str, object] | None,
    ) -> None:
        from langsmith.sandbox import ResourceNotFoundError  # noqa: PLC0415

        template = sandbox.options.get("proxy_config")
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
            raise RuntimeError("Could not update sandbox proxy credentials.") from None
        self._hashes[name] = digest


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
