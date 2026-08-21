import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_openai.chat_models.codex import _ChatOpenAICodex  # noqa: PLC2701
from langchain_openai.chatgpt_oauth import (  # noqa: PLC2701
    _ChatGPTOAuthTokenProvider,
    _ChatGPTToken,
)

_BROKER_URL_ENV = "OPEN_SWE_OPENAI_OAUTH_BROKER_URL"
_BROKER_TOKEN_ENV = "OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN"
_BROKER_MANAGED_REFRESH_TOKEN = "managed-by-desktop-broker"


def _broker_config() -> tuple[str, str] | None:
    url = os.environ.get(_BROKER_URL_ENV, "")
    token = os.environ.get(_BROKER_TOKEN_ENV, "")
    if not url or not token:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/token":
        return None
    return url, token


def desktop_openai_oauth_available() -> bool:
    return _broker_config() is not None


class _DesktopOpenAIOAuthTokenProvider(_ChatGPTOAuthTokenProvider):
    def __init__(self, broker_url: str, broker_token: str) -> None:
        self._broker_url = broker_url
        self._broker_token = broker_token
        self._current_token: _ChatGPTToken | None = None

    def get_token(self) -> _ChatGPTToken:
        if self._current_token is None:
            raise RuntimeError("Local OpenAI credentials have not been fetched asynchronously")
        return self._current_token

    async def aget_token(self) -> _ChatGPTToken:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self._broker_url,
                headers={"Authorization": f"Bearer {self._broker_token}"},
            )
        response.raise_for_status()
        payload = response.json()
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        account_id = payload.get("account_id") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Local OpenAI credential broker returned no access token")
        if (
            not isinstance(account_id, str)
            or not account_id
            or len(account_id) > 512
            or "\r" in account_id
            or "\n" in account_id
        ):
            raise ValueError("Local OpenAI credential broker returned no account ID")
        token = _ChatGPTToken(
            access_token=access_token,
            refresh_token=_BROKER_MANAGED_REFRESH_TOKEN,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            account_id=account_id,
        )
        self._current_token = token
        return token

    def get_access_token(self) -> str:
        return self.get_token().access_token

    async def aget_access_token(self) -> str:
        return (await self.aget_token()).access_token


def build_desktop_openai_oauth_model(model_name: str, **kwargs: Any) -> BaseChatModel:
    config = _broker_config()
    if config is None:
        raise ValueError("Local OpenAI credentials are unavailable")
    provider = _DesktopOpenAIOAuthTokenProvider(*config)
    return _ChatOpenAICodex(
        model=model_name,
        token_provider=provider,
        originator="open_swe_desktop",
        **kwargs,
    )
