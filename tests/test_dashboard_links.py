import importlib

from agent.utils import dashboard_links


def test_dashboard_review_url_points_to_reviews_page(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.com")
    importlib.reload(dashboard_links)
    url = dashboard_links.dashboard_review_url("owner", "repo", 42)
    assert url == "https://example.com/agents/reviews/owner/repo/42"


def test_dashboard_review_url_escapes_segments(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.com")
    importlib.reload(dashboard_links)
    url = dashboard_links.dashboard_review_url("my org", "my/repo", 7)
    assert url == "https://example.com/agents/reviews/my%20org/my%2Frepo/7"


def test_dashboard_review_url_requires_fields(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://example.com")
    importlib.reload(dashboard_links)
    assert dashboard_links.dashboard_review_url("", "repo", 1) is None
    assert dashboard_links.dashboard_review_url("owner", "repo", 0) is None
