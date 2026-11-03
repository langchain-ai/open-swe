import pytest

from openswe.dashboard.desktop_environments import DesktopEnvironment, alternative_environments


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
