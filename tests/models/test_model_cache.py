from unittest.mock import patch

import pytest

from agent.utils import model


class _ClosableModel:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_evict_cached_model_closes_model_and_forces_fresh_construction() -> None:
    model._MODEL_CACHE.clear()
    constructed: list[_ClosableModel] = []

    def build_model(*args: object, **kwargs: object) -> _ClosableModel:
        result = _ClosableModel()
        constructed.append(result)
        return result

    try:
        with patch.object(model, "init_chat_model", build_model):
            first = model.make_model("ollama:test", use_gateway=False)
            await model.evict_cached_model(first)
            second = model.make_model("ollama:test", use_gateway=False)
    finally:
        model._MODEL_CACHE.clear()

    assert first is not second
    assert constructed == [first, second]
    assert first.closed is True


def test_is_dead_client_error_walks_exception_chain() -> None:
    cause = RuntimeError("Cannot send a request, as the client has been closed")
    error = RuntimeError("provider request failed")
    error.__cause__ = cause

    assert model.is_dead_client_error(error) is True
