"""Exercise the public backend with the native Deep Agents file contract."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from managed_deepagents import runtime
from managed_deepagents._managed_tools import managed_runtime_overrides


@pytest.mark.parametrize("use_async", [False, True])
def test_runtime_backend_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_async: bool
) -> None:
    backends = pytest.importorskip("deepagents.backends")
    protocol = pytest.importorskip("deepagents.backends.protocol")
    sandboxes = {}
    for thread in ["first", "second"]:
        root = tmp_path / thread
        root.mkdir()
        sandboxes[thread] = backends.FilesystemBackend(root_dir=root, virtual_mode=True)

    def resolve(_sandbox: object, config: Any, *_args: object, **_kwargs: object) -> object:
        return sandboxes[config["configurable"]["thread_id"]]

    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)
    source = runtime._managed_sandbox_backend(SimpleNamespace(kind="sandbox", options={}), None)

    def runtime_backend(thread: str) -> Any:
        extras = managed_runtime_overrides(
            SimpleNamespace(config={"configurable": {"thread_id": thread}}),
            source.bind_runtime,
        )
        assert "files" not in extras
        return extras["backend"]

    first = runtime_backend("first")
    second = runtime_backend("second")

    async def call(backend: Any, method: str, *args: object, **kwargs: object) -> Any:
        if use_async:
            return await getattr(backend, "a" + method)(*args, **kwargs)
        return getattr(backend, method)(*args, **kwargs)

    async def run() -> None:
        await call(first, "write", "/workspace/report.txt", "first\nline two\n")
        await call(second, "write", "/workspace/report.txt", "second\n")
        assert await call(first, "read", "/workspace/report.txt", 1, 1) == sandboxes["first"].read(
            "/workspace/report.txt", 1, 1
        )
        assert await call(second, "read", "/workspace/report.txt") == sandboxes["second"].read(
            "/workspace/report.txt"
        )
        assert (
            await call(first, "edit", "/workspace/report.txt", "first", "updated")
        ).occurrences == 1
        assert (await call(first, "grep", "updated", "/workspace", "*.txt")).matches
        assert await call(first, "glob", "*.txt", "/workspace") == sandboxes["first"].glob(
            "*.txt", "/workspace"
        )
        assert await call(first, "ls", "/workspace") == sandboxes["first"].ls("/workspace")
        if "max_count" in inspect.signature(protocol.BackendProtocol.grep).parameters:
            await call(first, "write", "/workspace/other.txt", "updated")
            result = await call(first, "grep", "updated", "/workspace", "*.txt", max_count=1)
            assert len(result.matches) == 1
            assert result.truncated
        content = bytes([0, 255, 128])
        uploaded = await call(first, "upload_files", [("/workspace/data.bin", content)])
        assert uploaded[0].error is None
        downloaded = await call(first, "download_files", ["/workspace/data.bin"])
        assert downloaded[0].content == content
        assert downloaded[0].error is None
        # Delete is optional in the upstream protocol; older harnesses omit it.
        if hasattr(sandboxes["first"], "delete"):
            assert (
                await call(first, "delete", "/workspace/report.txt")
            ).path == "/workspace/report.txt"
            assert (await call(first, "read", "/workspace/report.txt")).error
        assert await call(second, "read", "/workspace/report.txt") == sandboxes["second"].read(
            "/workspace/report.txt"
        )

    asyncio.run(run())
