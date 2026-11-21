"""The proxy callback commands in a thread's sandbox get their GitHub and tool-channel
credentials from.

A thread's sandbox carries the same proxy config on every run, with no credential in it
(``SandboxProxyCallback.proxy_config``). When a command there calls GitHub or the
thread's tool channel, LangSmith's proxy asks this callback for the request's headers,
signed for the sandbox the request leaves from, and the backend answers for the thread
the sandbox belongs to while that thread is still bound to it. The proxy keeps an answer
for ``CALLBACK_TTL_SECONDS``.
"""

import logging
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Final, NoReturn, Self
from urllib.parse import quote, urlsplit

import httpx2
from fastapi import HTTPException
from langsmith.sandbox import (
    SandboxCallback,
    SandboxCallbackIdentity,
    SandboxTokenVerificationError,
    SandboxTokenVerifier,
)
from pydantic import JsonValue
from runtime_api.sandbox_proxy import GITHUB_PROXY_RULES

from openswe.dashboard.langsmith_oauth import langsmith_issuer
from openswe.sandboxes.lifecycle import SandboxCredentialPolicy
from openswe.sandboxes.providers.langsmith import sandbox_proxy_config, service_identity_jwks_url
from openswe.sandboxes.providers.langsmith_api import SandboxAPI
from openswe.sandboxes.proxy_callback_routes import ProxyCallbackAnswer
from openswe.sandboxes.tool_access import (
    PROXY_CALLBACK_PATH,
    TOOLS_HEADER,
    TOOLS_URL_ENV,
    issue_tool_access,
    tools_base_url,
)
from openswe.threads.index import ThreadSearch
from openswe.threads.store import THREADS
from openswe.utils.dashboard_links import sandbox_api_base_url
from openswe.utils.langsmith import resolve_tenant_id
from openswe.utils.time import parse_iso

logger = logging.getLogger(__name__)

# How long LangSmith's proxy keeps an answer for a host: how long a sandbox its thread
# moved off can still use the thread's credentials, and a token GitHub refused stays in use.
CALLBACK_TTL_SECONDS: Final = 300
# A rule no request matches, carrying the sandbox's ``env_vars`` in the config an MDA
# deployment from before ``SandboxCreate.env_vars`` writes (``legacy_config``).
ENV_RULE: Final = "open-swe-proxy-env"
_ENV_RULE_MATCH: Final = "x-open-swe-proxy-env: never-sent"
# More threads than this bound to one sandbox are not read; such a sandbox gets nothing.
_BOUND_SCAN: Final = 200
_LOOKUP_SECONDS: Final = 10.0
_NAME_CACHE_LIMIT: Final = 4096


class SandboxProxyCallback:
    """The callback a thread's sandbox's proxy config names, and the config itself."""

    _verifier: ClassVar[SandboxTokenVerifier | None] = None
    # A sandbox's name by its id; neither ever changes.
    _names: ClassVar[OrderedDict[str, str]] = OrderedDict()

    @staticmethod
    def url() -> str | None:
        """Where LangSmith's proxy calls back; None where sandboxes have no public HTTPS
        origin to reach the backend through, as in local development without a tunnel."""
        base = sandbox_api_base_url()
        return None if base is None else base + PROXY_CALLBACK_PATH

    @staticmethod
    def hosts() -> list[str]:
        """The hosts whose requests the callback answers for: GitHub's, and the tool
        channel's where the backend has one."""
        hosts = [host for rule in GITHUB_PROXY_RULES for host in rule.hosts]
        tools = tools_base_url()
        tools_host = urlsplit(tools).hostname if tools else None
        return [*hosts, *([tools_host] if tools_host else [])]

    @classmethod
    def proxy_config(
        cls,
        base_proxy_config: Mapping[str, object] | None,
        *,
        federated_rules: Sequence[dict[str, JsonValue]] | None = None,
    ) -> dict[str, JsonValue]:
        """The proxy config of a thread's sandbox: the custom settings of
        ``base_proxy_config``, the sandbox's cloud identity rules (``federated_rules``, or the
        base config's for a sandbox not yet created), and this callback for the hosts it
        answers for. Without a public origin there is no callback, and commands reach those
        hosts without credentials."""
        config = sandbox_proxy_config(
            None, base_proxy_config=base_proxy_config, federated_rules=federated_rules
        )
        url = cls.url()
        if url is None:
            return config
        callbacks = config.get("callbacks")
        config["callbacks"] = [
            {"match_hosts": list(cls.hosts()), "url": url, "ttl_seconds": CALLBACK_TTL_SECONDS},
            *(
                callback
                for callback in (callbacks if isinstance(callbacks, list) else [])
                if not (isinstance(callback, dict) and callback.get("url") == url)
            ),
        ]
        return config

    @classmethod
    def env_vars(cls, *, github: bool) -> dict[str, str]:
        """What commands in a thread's sandbox find in their environment, beside the
        credentials the callback injects on the wire: the sandbox's scripts read the tool
        channel's URL from it, and ``gh``, which refuses to run without a token there, finds a
        placeholder the callback replaces where it answers with one (``github``). Without a
        token to replace it the placeholder is empty, which ``gh`` reads as none, instead of
        one GitHub refuses. LangSmith merges a sandbox's environment key by key and keeps one
        a snapshot carried, so the placeholder is always named. None of it is a credential;
        without a callback there is none of it."""
        if cls.url() is None:
            return {}
        env_vars = {
            name: value if github else ""
            for rule in GITHUB_PROXY_RULES
            for name, value in rule.env_vars.items()
        }
        if (tools := tools_base_url()) is not None:
            env_vars[TOOLS_URL_ENV] = tools
        return env_vars

    @classmethod
    def legacy_config(
        cls, proxy_config: dict[str, JsonValue], env_vars: Mapping[str, str]
    ) -> dict[str, JsonValue]:
        """``proxy_config`` with the sandbox's ``env_vars`` on a rule no request matches, for
        an MDA deployment that creates sandboxes without them."""
        if not env_vars:
            return proxy_config
        rules = proxy_config.get("rules")
        return {
            **proxy_config,
            "rules": [
                *(rules if isinstance(rules, list) else []),
                {
                    "name": ENV_RULE,
                    "match_hosts": list(GITHUB_PROXY_RULES[0].hosts),
                    "match_headers": [_ENV_RULE_MATCH],
                    "headers": [],
                    "env_vars": dict(env_vars),
                },
            ],
        }

    @classmethod
    async def answer(cls, body: bytes, signature: str | None) -> ProxyCallbackAnswer:
        """The headers for the request a sandbox's proxy signed ``body`` for; raises
        ``HTTPException`` for a request that is not LangSmith's, or for a sandbox no thread
        of the backend's is bound to."""
        url = cls.url()
        if url is None:
            raise HTTPException(404, "This backend has no proxy callback")
        callback = await cls._verified(body, signature, url)
        sandbox = await CallingSandbox.of(callback.identity)
        return await sandbox.headers_for(callback.host)

    @classmethod
    async def _verified(cls, body: bytes, signature: str | None, url: str) -> SandboxCallback:
        if cls._verifier is None:
            cls._verifier = SandboxTokenVerifier(jwks_url=service_identity_jwks_url())
        try:
            return await cls._verifier.averify_callback(
                body=body, signature=signature or "", aud=url, issuer=langsmith_issuer()
            )
        except SandboxTokenVerificationError as exc:
            logger.warning("Refused a sandbox proxy callback", extra={"cause": str(exc)})
            raise HTTPException(401, "Invalid sandbox proxy callback signature") from exc

    @classmethod
    async def sandbox_name(cls, sandbox_id: str) -> str | None:
        """The name of sandbox ``sandbox_id`` in the backend's LangSmith workspace; None for
        one it cannot see."""
        if (name := cls._names.get(sandbox_id)) is not None:
            cls._names.move_to_end(sandbox_id)
            return name
        try:
            response = await SandboxAPI.from_env().request(
                "GET", f"/boxes/{quote(sandbox_id, safe='')}", timeout=_LOOKUP_SECONDS
            )
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        name = response.json().get("name")
        if not isinstance(name, str) or not name:
            return None
        cls._names[sandbox_id] = name
        while len(cls._names) > _NAME_CACHE_LIMIT:
            cls._names.popitem(last=False)
        return name


@dataclass(frozen=True, slots=True)
class CallingSandbox:
    """The sandbox a proxy callback came from, and the thread it belongs to: the first bound
    to it that is not a task worker sharing it. A copy of a thread keeps its sandbox, and is
    always made after it, so the sandbox stays the original's."""

    name: str
    thread_id: str
    metadata: Mapping[str, JsonValue]

    @classmethod
    async def of(cls, identity: SandboxCallbackIdentity) -> Self:
        """The sandbox ``identity`` names; raises ``HTTPException`` (403) for one of another
        workspace, or one that no thread is bound to."""
        tenant_id = await resolve_tenant_id()
        if tenant_id is None or identity.tenant_id.lower() != tenant_id.lower():
            cls._refuse("The sandbox belongs to another workspace", identity)
        name = await SandboxProxyCallback.sandbox_name(identity.sandbox_id)
        if name is None:
            cls._refuse("The backend cannot see the sandbox", identity)
        bound = await THREADS.search(
            ThreadSearch(
                metadata={"sandbox_id": name},
                sort_by="created_at",
                sort_order="asc",
                limit=_BOUND_SCAN,
            )
        )
        owners = [thread for thread in bound if thread.metadata.get("task_id") is None]
        if len(bound) >= _BOUND_SCAN or not owners:
            cls._refuse("No thread is bound to the sandbox", identity)
        owner = owners[0]
        if owner.metadata.get("sandbox_owner") == "external":
            cls._refuse("An external sandbox keeps its own proxy config", identity)
        return cls(name=name, thread_id=owner.thread_id, metadata=owner.metadata)

    @staticmethod
    def _refuse(reason: str, identity: SandboxCallbackIdentity) -> NoReturn:
        logger.warning(
            "Refused a sandbox proxy callback",
            extra={
                "cause": reason,
                "callback_tenant_id": identity.tenant_id,
                "sandbox_uuid": identity.sandbox_id,
            },
        )
        raise HTTPException(403, reason)

    async def headers_for(self, host: str) -> ProxyCallbackAnswer:
        """The headers a request to ``host`` from this sandbox carries: the thread's tool
        channel capability, or a GitHub token scoped to the thread's repositories; none for a
        thread that may not use GitHub or a host the callback does not answer for."""
        host = host.lower().rstrip(".")
        tools = tools_base_url()
        if tools is not None and urlsplit(tools).hostname == host:
            access = await issue_tool_access(self.thread_id, self.name)
            return ProxyCallbackAnswer(headers={TOOLS_HEADER: access[1]} if access else {})
        rule = next((rule for rule in GITHUB_PROXY_RULES if rule.matches(host)), None)
        if rule is None:
            return ProxyCallbackAnswer()
        access = await SandboxCredentialPolicy.proxy_token(self.thread_id, self.metadata)
        if not access.token:
            return ProxyCallbackAnswer()
        return ProxyCallbackAnswer(
            headers={"Authorization": rule.authorization(access.token)},
            expires_at=parse_iso(access.expires_at),
        )
