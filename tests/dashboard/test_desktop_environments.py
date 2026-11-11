import pytest

from openswe.dashboard.desktop_environments import (
    DesktopEnvironment,
    alternative_environments,
    api_desktop_environments,
)
from openswe.utils.dashboard_links import PublicOrigin


def test_alternative_environments_keep_url_colons_and_skip_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OPENSWE_ALTERNATIVE_ENVS",
        "preview:https://preview.example:8443/app, staging: https://staging.example ,bogus,ftp:ftp://x",
    )
    assert alternative_environments() == [
        DesktopEnvironment(name="preview", url="https://preview.example:8443/app"),
        DesktopEnvironment(name="staging", url="https://staging.example"),
    ]


def test_dashboard_origins_list_the_configured_origin_then_its_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://robin.example/")
    monkeypatch.setenv(
        "DASHBOARD_ALIAS_URLS", "https://openswe.example, https://ROBIN.example, not a url"
    )
    assert PublicOrigin.all() == ["https://robin.example", "https://openswe.example"]


def test_dashboard_origins_are_empty_without_a_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_ALIAS_URLS", raising=False)
    monkeypatch.setattr("openswe.utils.dashboard_links.is_single_origin", lambda: False)
    assert PublicOrigin.all() == []


async def test_desktop_environments_advertise_the_dashboard_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://robin.example/robin/")
    response = await api_desktop_environments()
    assert response.dashboard_base_url == "https://robin.example/robin"
