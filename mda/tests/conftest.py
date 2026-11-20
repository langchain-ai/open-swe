import pytest
from managed_deepagents import _sandbox_binding


@pytest.fixture(autouse=True)
def sandbox_bindings(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str | None]]:
    """Each thread's sandbox binding (``ls_mda_sandbox_binding``), as ``{thread: {name: id}}``,
    kept here instead of in the Agent Server's thread metadata, which tests have none of."""
    bindings: dict[str, dict[str, str | None]] = {}

    def read(thread_id: str, *, deployed: bool) -> dict[str, str | None] | None:
        return dict(bindings.get(thread_id, {})) if deployed else None

    def record(thread_id: str, name: str, sandbox_id: str | None, _bound: object) -> None:
        bindings.setdefault(thread_id, {})[name] = sandbox_id

    monkeypatch.setattr(_sandbox_binding, "read_bound_sandboxes", read)
    monkeypatch.setattr(_sandbox_binding, "record_bound_sandbox", record)
    return bindings
