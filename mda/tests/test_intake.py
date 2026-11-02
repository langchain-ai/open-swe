import pytest
from rt.intake import backend_endpoint


def test_the_channel_asks_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.example/")
    assert (
        backend_endpoint("/linear/channel/events")
        == "https://backend.example/linear/channel/events"
    )

    # MDA reads the channels to build the deployment, without its environment:
    # a host that never resolves stands in, so a call fails instead of misrouting.
    monkeypatch.delenv("OPEN_SWE_BACKEND_URL")
    assert backend_endpoint("/x") == "https://open-swe-backend.invalid/x"
