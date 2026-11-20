import importlib
import json

sandbox_output = importlib.import_module("openswe.tools.sandbox_output")


def _decode_jsonl(content: str) -> str:
    return "".join(json.loads(line)["text"] for line in content.splitlines())


def test_chunk_output_as_jsonl_is_lossless_and_bounds_source_lines() -> None:
    content = "prefix\n" + "x" * 10_000 + "\nsuffix"

    encoded = sandbox_output.chunk_output_as_jsonl(content)

    assert _decode_jsonl(encoded) == content
    assert max(map(len, encoded.splitlines())) < 5_000


async def test_write_sandbox_output_uses_current_thread_backend(monkeypatch) -> None:
    writes: list[tuple[str, str]] = []

    class Backend:
        async def awrite(self, path: str, content: str) -> dict[str, None]:
            writes.append((path, content))
            return {"error": None}

    backend = Backend()

    async def fake_get_backend(thread_id: str) -> Backend:
        assert thread_id == "thread-123"
        return backend

    async def fake_resolve_work_dir(value: Backend) -> str:
        assert value is backend
        return "/workspace"

    monkeypatch.setattr(
        "openswe.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-123"}}
    )
    monkeypatch.setattr(sandbox_output, "get_sandbox_backend", fake_get_backend)
    monkeypatch.setattr(sandbox_output, "resolve_sandbox_work_dir", fake_resolve_work_dir)

    path = await sandbox_output.write_sandbox_output("web-search", "full results", "txt")

    assert path.startswith("/workspace/web-search-")
    assert path.endswith(".txt")
    assert writes == [(path, "full results")]
