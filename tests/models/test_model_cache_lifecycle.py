from collections.abc import Iterator
from typing import Any

import pytest

from agent.api.app import app, lifespan
from agent.utils import model


class _Model:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def ainvoke(self) -> str:
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed")
        return "ok"


@pytest.fixture(autouse=True)
def _clear_model_cache() -> Iterator[None]:
    model._MODEL_CACHE.clear()
    yield
    model._MODEL_CACHE.clear()


@pytest.mark.asyncio
async def test_app_shutdown_preserves_cached_model_references(monkeypatch) -> None:
    cached_model = _Model()
    monkeypatch.setattr(model, "init_chat_model", lambda **_kwargs: cached_model)

    retained_model = model.make_model("fireworks:test", use_gateway=False)
    async with lifespan(app):
        pass

    assert await retained_model.ainvoke() == "ok"


@pytest.mark.asyncio
async def test_explicit_cache_shutdown_replaces_closed_models(monkeypatch) -> None:
    models: list[_Model] = []

    def create_model(**_kwargs: Any) -> _Model:
        created = _Model()
        models.append(created)
        return created

    monkeypatch.setattr(model, "init_chat_model", create_model)

    first = model.make_model("fireworks:test", use_gateway=False)
    await model.close_cached_models()
    second = model.make_model("fireworks:test", use_gateway=False)

    assert first.closed is True
    assert second is not first
    assert await second.ainvoke() == "ok"
