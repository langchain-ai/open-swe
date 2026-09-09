from unittest.mock import AsyncMock, Mock

import pytest

from agent.analytics import database, outbox
from agent.api import app as app_module
from agent.sandboxes.providers import registry
from agent.utils import model


@pytest.mark.asyncio
async def test_lifespan_runs_analytics_lifecycle(monkeypatch) -> None:
    migrate = AsyncMock()
    start = Mock()
    stop = AsyncMock()
    close = AsyncMock()
    close_models = AsyncMock()
    monkeypatch.setattr(database, "migrate", migrate)
    monkeypatch.setattr(outbox, "start_worker", start)
    monkeypatch.setattr(outbox, "stop_worker", stop)
    monkeypatch.setattr(database, "close", close)
    monkeypatch.setattr(registry, "validate_sandbox_startup_config", Mock())
    monkeypatch.setattr(model, "validate_local_dev_llm_config", Mock())
    monkeypatch.setattr(model, "close_cached_models", close_models)

    async with app_module.lifespan(app_module.app):
        migrate.assert_awaited_once()
        start.assert_called_once()

    stop.assert_awaited_once()
    close.assert_awaited_once()
    close_models.assert_awaited_once()
