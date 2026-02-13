"""Sandbox creation and configuration utilities."""

import os

from deepagents.backends.protocol import SandboxBackendProtocol

from ..integrations.langsmith import LangSmithProvider


def _get_langsmith_api_key() -> str | None:
    """Get LangSmith API key from environment.

    Checks LANGSMITH_API_KEY first, then falls back to LANGSMITH_API_KEY_PROD
    for LangGraph Cloud deployments where LANGSMITH_API_KEY is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def _get_sandbox_template_config() -> tuple[str | None, str | None]:
    """Get sandbox template configuration from environment.

    Returns:
        Tuple of (template_name, template_image) from environment variables.
        Values are None if not set in environment.
    """
    template_name = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_NAME")
    template_image = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_IMAGE")
    return template_name, template_image


def _create_langsmith_sandbox(
    sandbox_id: str | None = None,
) -> SandboxBackendProtocol:
    """Create or connect to a LangSmith sandbox without automatic cleanup.

    This function directly uses the LangSmithProvider to create/connect to sandboxes
    without the context manager cleanup, allowing sandboxes to persist across
    multiple agent invocations.

    Args:
        sandbox_id: Optional existing sandbox ID to connect to.
                   If None, creates a new sandbox.

    Returns:
        SandboxBackendProtocol instance
    """
    api_key = _get_langsmith_api_key()
    template_name, template_image = _get_sandbox_template_config()

    provider = LangSmithProvider(api_key=api_key)
    return provider.get_or_create(
        sandbox_id=sandbox_id,
        template=template_name,
        template_image=template_image,
    )
