import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.threads import local_writes
from openswe.threads.local_writes import (
    _CREATES,
    _OPS_MODULES,
    LocalAgentServerWriteError,
    LocalWrites,
)


async def test_the_sealed_agent_server_creates_no_thread_run_or_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_RUNTIME_EDITION", "inmem")
    operations = [
        (getattr(importlib.import_module(module), class_name), method)
        for module in _OPS_MODULES
        for class_name, method in _CREATES
    ]
    for ops, method in operations:
        # Put back after the test whatever the seal replaces.
        monkeypatch.setattr(ops, method, ops.__dict__[method])

    LocalWrites().seal()

    for ops, method in operations:
        with pytest.raises(LocalAgentServerWriteError) as refused:
            await getattr(ops, method)(None, "00000000-0000-4000-8000-000000000001")
        assert refused.value.status_code == 410


@pytest.mark.parametrize("missing", [False, True])
async def test_operations_can_be_split_between_runtime_and_grpc(monkeypatch, missing):
    threads = type("Threads", (), {"put": AsyncMock(), "copy": AsyncMock()})
    runs = type("Runs", (), {"put": AsyncMock()})
    crons = type("Crons", (), {"put": AsyncMock()})
    modules = {
        "langgraph_runtime.ops": SimpleNamespace(Crons=crons),
        "langgraph_api.grpc.ops": SimpleNamespace(
            Threads=threads, **({} if missing else {"Runs": runs})
        ),
    }
    monkeypatch.setattr(local_writes.importlib, "import_module", modules.__getitem__)
    seal = LocalWrites()
    if missing:
        with pytest.raises(RuntimeError, match="Runs"):
            seal.seal()
        assert seal.sealed == []
        return
    seal.seal()
    for ops, method in ((threads, "put"), (threads, "copy"), (runs, "put"), (crons, "put")):
        with pytest.raises(LocalAgentServerWriteError):
            await getattr(ops, method)()
