"""LangSmith sandbox backend integration."""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from langsmith.sandbox import SandboxClient, SandboxTemplate

logger = logging.getLogger(__name__)


def _get_langsmith_api_key() -> str | None:
    """Get LangSmith API key from environment.

    Checks LANGSMITH_API_KEY first, then falls back to LANGSMITH_API_KEY_PROD
    for LangGraph Cloud deployments where LANGSMITH_API_KEY is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def _get_sandbox_template_config() -> tuple[str | None, str | None]:
    """Get sandbox template configuration from environment."""
    template_name = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_NAME")
    template_image = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_IMAGE")
    return template_name, template_image


def _configure_github_proxy(sandbox_name: str, github_token: str) -> None:
    """Configure sandbox proxy to inject GitHub auth for all github.com requests.

    Uses the LangSmith proxy-config API to set up header injection so that
    git operations (clone, pull, push) authenticate via the proxy rather than
    writing credentials to disk in the sandbox.

    Args:
        sandbox_name: The sandbox name/ID returned by the LangSmith API.
        github_token: GitHub token to inject as Authorization header.
    """
    api_key = _get_langsmith_api_key()
    if not api_key:
        logger.warning("No LangSmith API key found, skipping GitHub proxy configuration")
        return
    langsmith_endpoint = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    url = f"{langsmith_endpoint}/v2/sandboxes/boxes/{sandbox_name}"
    basic_auth = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
    payload = {
        "proxy_config": {
            "rules": [
                {
                    "name": "github",
                    "match_hosts": ["github.com", "*.github.com"],
                    "headers": [
                        {
                            "name": "Authorization",
                            "type": "opaque",
                            "value": f"Basic {basic_auth}",
                        }
                    ],
                }
            ]
        }
    }
    with httpx.Client() as client:
        response = client.patch(
            url,
            json=payload,
            headers={"X-API-Key": api_key},
        )
        response.raise_for_status()
    logger.info("Configured GitHub proxy for sandbox %s", sandbox_name)


def create_langsmith_sandbox(
    sandbox_id: str | None = None,
    github_token: str | None = None,
) -> SandboxBackendProtocol:
    """Create or connect to a LangSmith sandbox without automatic cleanup.

    This function directly uses the LangSmithProvider to create/connect to sandboxes
    without the context manager cleanup, allowing sandboxes to persist across
    multiple agent invocations.

    Args:
        sandbox_id: Optional existing sandbox ID to connect to.
                   If None, creates a new sandbox.
        github_token: Optional GitHub token. Used to configure proxy auth on
                      new sandboxes. Ignored when connecting to an existing sandbox.

    Returns:
        SandboxBackendProtocol instance
    """
    api_key = _get_langsmith_api_key()
    template_name, template_image = _get_sandbox_template_config()

    provider = LangSmithProvider(api_key=api_key)
    backend = provider.get_or_create(
        sandbox_id=sandbox_id,
        template=template_name,
        template_image=template_image,
    )
    _update_thread_sandbox_metadata(backend.id)

    if sandbox_id is None and github_token:
        _configure_github_proxy(backend.id, github_token)

    return backend


def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Update thread metadata with sandbox_id."""
    try:
        import asyncio

        from langgraph.config import get_config
        from langgraph_sdk import get_client

        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return
        client = get_client()

        async def _update() -> None:
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": sandbox_id},
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_update())
        else:
            loop.create_task(_update())
    except Exception:
        pass


class SandboxProvider(ABC):
    """Interface for creating and deleting sandbox backends."""

    @abstractmethod
    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get an existing sandbox, or create one if needed."""
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        *,
        sandbox_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete a sandbox by id."""
        raise NotImplementedError


DEFAULT_TEMPLATE_NAME = "open-swe-new"
DEFAULT_TEMPLATE_IMAGE = "python:3"


class LangSmithProvider(SandboxProvider):
    """LangSmith sandbox provider implementation."""

    def __init__(self, api_key: str | None = None) -> None:
        from langsmith import sandbox

        self._api_key = api_key or _get_langsmith_api_key()
        if not self._api_key:
            msg = "LANGSMITH_API_KEY (or LANGSMITH_API_KEY_PROD) not set"
            raise ValueError(msg)
        self._client: SandboxClient = sandbox.SandboxClient(api_key=self._api_key)

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        template: str | None = None,
        template_image: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get existing or create new LangSmith sandbox."""
        if kwargs:
            msg = f"Received unsupported arguments: {list(kwargs.keys())}"
            raise TypeError(msg)
        if sandbox_id:
            try:
                sandbox = self._client.get_sandbox(name=sandbox_id)
            except Exception as e:
                msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
                raise RuntimeError(msg) from e
            return LangSmithSandbox(sandbox)

        resolved_template_name, resolved_image_name = self._resolve_template(
            template, template_image
        )

        self._ensure_template(resolved_template_name, resolved_image_name)

        try:
            sandbox = self._client.create_sandbox(
                template_name=resolved_template_name, timeout=timeout
            )
        except Exception as e:
            msg = f"Failed to create sandbox from template '{resolved_template_name}': {e}"
            raise RuntimeError(msg) from e

        for _ in range(timeout // 2):
            try:
                result = sandbox.run("echo ready", timeout=5)
                if result.exit_code == 0:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            with contextlib.suppress(Exception):
                self._client.delete_sandbox(sandbox.name)
            msg = f"LangSmith sandbox failed to start within {timeout} seconds"
            raise RuntimeError(msg)

        return LangSmithSandbox(sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a LangSmith sandbox."""
        self._client.delete_sandbox(sandbox_id)

    @staticmethod
    def _resolve_template(
        template: SandboxTemplate | str | None,
        template_image: str | None = None,
    ) -> tuple[str, str]:
        """Resolve template name and image from kwargs."""
        resolved_image = template_image or DEFAULT_TEMPLATE_IMAGE
        if template is None:
            return DEFAULT_TEMPLATE_NAME, resolved_image
        if isinstance(template, str):
            return template, resolved_image
        if template_image is None and template.image:
            resolved_image = template.image
        return template.name, resolved_image

    def _ensure_template(
        self,
        template_name: str,
        template_image: str,
    ) -> None:
        """Ensure template exists, creating it if needed."""
        from langsmith.sandbox import ResourceNotFoundError

        try:
            self._client.get_template(template_name)
        except ResourceNotFoundError as e:
            if e.resource_type != "template":
                msg = f"Unexpected resource not found: {e}"
                raise RuntimeError(msg) from e
            try:
                self._client.create_template(name=template_name, image=template_image)
            except Exception as create_err:
                msg = f"Failed to create template '{template_name}': {create_err}"
                raise RuntimeError(msg) from create_err
        except Exception as e:
            msg = f"Failed to check template '{template_name}': {e}"
            raise RuntimeError(msg) from e
