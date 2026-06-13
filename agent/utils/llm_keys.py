"""Startup validation for LLM provider API keys.

Without this check, missing or invalid LLM keys only surface on the first
real model invocation, which is confusing for new contributors. See #1437.

The validator runs from the FastAPI lifespan hook and (optionally) at module
import for graph-only entry points (``langgraph dev``). It:

1. Resolves the configured model (``LLM_MODEL_ID`` env var, falling back to
   ``server.DEFAULT_LLM_MODEL_ID``) and the provider prefix it implies.
2. Verifies the matching env var (``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` /
   ``GOOGLE_API_KEY``) is present, non-empty, and not a known placeholder.
3. Makes a cheap, non-billable round-trip to confirm the key actually works:
       - OpenAI: ``models.list()`` (no params, no charge).
       - Anthropic: ``messages.create`` with ``max_tokens=1``.
       - Google: ``models.generate_content`` with ``max_output_tokens=1``.
4. Raises ``ValueError`` with installation/setup guidance on any failure.

Set ``OPEN_SWE_SKIP_LLM_KEY_VALIDATION=1`` to bypass (useful for CI and for
unit tests that intentionally exercise missing-key paths).
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

# Provider prefix -> (env var, human-readable provider name, setup URL).
_PROVIDER_ENV_VARS: Final[dict[str, tuple[str, str, str]]] = {
    "openai": ("OPENAI_API_KEY", "OpenAI", "https://platform.openai.com/api-keys"),
    "anthropic": (
        "ANTHROPIC_API_KEY",
        "Anthropic",
        "https://console.anthropic.com/settings/keys",
    ),
    "google": (
        "GOOGLE_API_KEY",
        "Google AI",
        "https://aistudio.google.com/apikey",
    ),
}

# Values that look like a real key by length/prefix but are obviously fake.
# We also reject the empty string and whitespace-only values regardless.
_PLACEHOLDER_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "",
        "your-key-here",
        "your_key_here",
        "your-openai-api-key",
        "your-anthropic-api-key",
        "sk-xxx",
        "sk-... ",
        "sk-placeholder",
        "<your_key>",
        "<your-key>",
        "<your_openai_api_key>",
        "<your_anthropic_api_key>",
        "changeme",
        "replace-me",
        "todo",
        "test",
        "fake",
    }
)


class LLMKeyValidationError(ValueError):
    """Raised when an LLM API key is missing, looks like a placeholder, or fails a live verification call.

    Inherits from ``ValueError`` so existing startup-error handling (and the
    lifespan hook in ``agent.webapp``) treats it like any other config error.
    """


def _resolve_model_id() -> str:
    """Read ``LLM_MODEL_ID`` from env or fall back to the server default."""
    # Import lazily so this module has zero cost when imported only for type
    # checking; also keeps ``agent.webapp`` from pulling in ``agent.server``
    # at import time (which would create a circular import on cold start).
    try:
        from agent.server import DEFAULT_LLM_MODEL_ID  # noqa: WPS433

        default = DEFAULT_LLM_MODEL_ID
    except Exception:  # noqa: BLE001  -- defensive: server.py pulls LangChain
        default = "openai:gpt-5.5"

    return os.environ.get("LLM_MODEL_ID", default)


def _provider_prefix(model_id: str) -> str | None:
    """Extract the provider prefix from a ``langchain`` model spec (``openai:``/``anthropic:``/etc).

    Returns ``None`` when the spec uses no prefix or an unrecognised one.
    """
    if ":" not in model_id:
        return None
    prefix = model_id.split(":", 1)[0].strip().lower()
    return prefix or None


def _looks_like_placeholder(key: str) -> bool:
    """Return True when a key string is empty, whitespace, or a known placeholder."""
    stripped = key.strip()
    if not stripped:
        return True
    if stripped.lower() in _PLACEHOLDER_TOKENS:
        return True
    # Catches "sk-xxx", "sk-...", "sk-1234" — anything that starts with the
    # OpenAI/Anthropic prefix but never reaches plausible key length.
    lowered = stripped.lower()
    if lowered.startswith("sk-") and len(lowered) < 20:
        return True
    if lowered.startswith("sk-ant-") and len(lowered) < 30:
        return True
    return False


def _format_guidance(
    env_var: str,
    provider: str,
    setup_url: str,
    *,
    model_id: str,
) -> str:
    """Build a human-readable error message with setup instructions."""
    return (
        f"{provider} API key for model '{model_id}' is missing or invalid.\n"
        f"  Required env var : {env_var}\n"
        f"  Get a key from   : {setup_url}\n"
        f"  Then export it   : export {env_var}=<your-real-key>\n"
        "  Or add it to your .env file (see INSTALLATION.md, step 6).\n"
        "  To skip this check (e.g. for local graph introspection), set "
        "OPEN_SWE_SKIP_LLM_KEY_VALIDATION=1."
    )


def _check_openai_key(api_key: str, model_id: str) -> None:
    """Verify an OpenAI key with ``models.list()`` (no params, no charge)."""
    try:
        from openai import OpenAI  # noqa: WPS433
    except ImportError as exc:
        raise LLMKeyValidationError(
            "openai package is not installed; cannot verify OPENAI_API_KEY. "
            "Reinstall with: uv pip install -e ."
        ) from exc

    try:
        client = OpenAI(api_key=api_key)
        client.models.list()
    except Exception as exc:  # noqa: BLE001
        raise LLMKeyValidationError(
            _format_guidance(
                "OPENAI_API_KEY",
                "OpenAI",
                _PROVIDER_ENV_VARS["openai"][2],
                model_id=model_id,
            )
            + f"\n  Underlying error : {type(exc).__name__}: {exc}"
        ) from exc


def _check_anthropic_key(api_key: str, model_id: str) -> None:
    """Verify an Anthropic key with a 1-token messages.create call."""
    try:
        import anthropic  # noqa: WPS433
    except ImportError as exc:
        raise LLMKeyValidationError(
            "anthropic package is not installed; cannot verify ANTHROPIC_API_KEY. "
            "Reinstall with: uv pip install -e ."
        ) from exc

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # 1 token is the minimum allowed by the Anthropic API. We never read
        # the response, so the cost is effectively zero.
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except Exception as exc:  # noqa: BLE001
        raise LLMKeyValidationError(
            _format_guidance(
                "ANTHROPIC_API_KEY",
                "Anthropic",
                _PROVIDER_ENV_VARS["anthropic"][2],
                model_id=model_id,
            )
            + f"\n  Underlying error : {type(exc).__name__}: {exc}"
        ) from exc


def _check_google_key(api_key: str, model_id: str) -> None:
    """Verify a Google AI key with a 1-token generate_content call."""
    try:
        import google.generativeai as genai  # noqa: WPS433
    except ImportError as exc:
        raise LLMKeyValidationError(
            "google-generativeai package is not installed; cannot verify "
            "GOOGLE_API_KEY. Reinstall with: uv pip install -e ."
        ) from exc

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        model.generate_content("ping", generation_config={"max_output_tokens": 1})
    except Exception as exc:  # noqa: BLE001
        raise LLMKeyValidationError(
            _format_guidance(
                "GOOGLE_API_KEY",
                "Google AI",
                _PROVIDER_ENV_VARS["google"][2],
                model_id=model_id,
            )
            + f"\n  Underlying error : {type(exc).__name__}: {exc}"
        ) from exc


def _check_live(provider: str, api_key: str, model_id: str) -> None:
    """Dispatch to the provider-specific live verification helper."""
    if provider == "openai":
        _check_openai_key(api_key, model_id)
        return
    if provider == "anthropic":
        _check_anthropic_key(api_key, model_id)
        return
    if provider == "google":
        _check_google_key(api_key, model_id)
        return
    # Unknown provider -> we already verified the env var exists; live check
    # is not implemented for it, so warn and move on.
    logger.warning(
        "Skipping live LLM key verification for unsupported provider '%s' "
        "(model '%s'); only presence/format was checked.",
        provider,
        model_id,
    )


def validate_llm_api_keys(
    *,
    perform_live_check: bool = True,
    model_id: str | None = None,
) -> str:
    """Validate the API key for the configured LLM at startup.

    Args:
        perform_live_check: When True (the default), also call the provider
            API to confirm the key actually works. Set False for unit tests
            and any environment where outbound network is undesirable.
        model_id: Override the model id (defaults to ``LLM_MODEL_ID`` env var,
            then ``server.DEFAULT_LLM_MODEL_ID``).

    Returns:
        The provider name that was validated (e.g. ``"openai"``).

    Raises:
        LLMKeyValidationError: When the key is missing, looks like a
            placeholder, or the live verification call fails.

    Behavior:
        Set ``OPEN_SWE_SKIP_LLM_KEY_VALIDATION=1`` to bypass the entire
        check (e.g. for offline graph introspection).
    """
    if os.environ.get("OPEN_SWE_SKIP_LLM_KEY_VALIDATION", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        logger.info("LLM API key validation skipped via OPEN_SWE_SKIP_LLM_KEY_VALIDATION")
        return ""

    resolved_model_id = model_id or _resolve_model_id()
    provider = _provider_prefix(resolved_model_id)

    if provider is None or provider not in _PROVIDER_ENV_VARS:
        logger.info(
            "LLM API key validation: model '%s' has no recognised provider "
            "prefix; skipping.",
            resolved_model_id,
        )
        return ""

    env_var, provider_name, _setup_url = _PROVIDER_ENV_VARS[provider]
    api_key = os.environ.get(env_var, "")

    if _looks_like_placeholder(api_key):
        raise LLMKeyValidationError(
            _format_guidance(
                env_var,
                provider_name,
                _setup_url,
                model_id=resolved_model_id,
            )
            + "\n  Detected reason : value is empty or matches a known placeholder."
        )

    if perform_live_check:
        _check_live(provider, api_key, resolved_model_id)

    logger.info(
        "LLM API key validated: provider=%s model=%s env_var=%s",
        provider,
        resolved_model_id,
        env_var,
    )
    return provider
