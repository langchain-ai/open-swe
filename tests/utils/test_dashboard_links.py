import pytest

from agent.utils.dashboard_links import dashboard_thread_id


@pytest.mark.parametrize(
    ("locator", "expected"),
    [
        ("thread-1", "thread-1"),
        (" b8d7d0b0-c1e3-5ded-bcc3-ef8254a34f2b ", "b8d7d0b0-c1e3-5ded-bcc3-ef8254a34f2b"),
        (
            "https://dev.open-swe.langchain.dev/agents/b8d7d0b0-c1e3-5ded-bcc3-ef8254a34f2b",
            "b8d7d0b0-c1e3-5ded-bcc3-ef8254a34f2b",
        ),
        (
            "<https://dev.open-swe.langchain.dev/agents/thread-1|Open in Web>",
            "thread-1",
        ),
        ("https://openswe.example/agents/thread%20one/plan?comment=1#section", "thread one"),
    ],
)
def test_dashboard_thread_id_accepts_ids_and_dashboard_urls(
    locator: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dev.open-swe.langchain.dev")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://openswe.example")
    assert dashboard_thread_id(locator) == expected


@pytest.mark.parametrize(
    "locator",
    [
        "",
        "/agents/thread-1",
        "https://openswe.example/agents",
        "https://openswe.example/agents/thread-1/extra",
        "https://openswe.example/agents/reviews/org/repo/1",
        "https://user@example.com/agents/thread-1",
        "ftp://openswe.example/agents/thread-1",
        "https://openswe.example/agents/thread%2Fchild",
        "https://openswe.example/agents/thread%ZZ",
        "https://evil.example/agents/thread-1",
    ],
)
def test_dashboard_thread_id_rejects_invalid_locators(
    locator: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example")
    assert dashboard_thread_id(locator) is None
