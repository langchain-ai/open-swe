from __future__ import annotations

import logging
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from managed_deepagents import channels
from managed_deepagents._channel_icon import (
    CHANNEL_ICON_MAX_BYTES,
    load_channel_icon,
    validate_channel_icon_path,
)
from managed_deepagents.runtime import build_managed_http_app

PNG = b"\x89PNG\r\n\x1a\n\x01\x02\x03"
JPEG = b"\xff\xd8\xff\xe0\x01\x02"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    # Icons are paths relative to channels/, as for the Slack channel.
    (tmp_path / "channels" / "assets").mkdir(parents=True)
    (tmp_path / "channels" / "assets" / "icon.png").write_bytes(PNG)
    return tmp_path


def test_takes_a_project_relative_png_or_jpeg_path() -> None:
    assert validate_channel_icon_path(" assets/icon.PNG ", "icon") == "assets/icon.PNG"
    assert validate_channel_icon_path("logo.jpeg", "icon") == "logo.jpeg"
    for path in (
        "",
        "/etc/icon.png",
        "C:/icon.png",
        "../icon.png",
        "assets/../../icon.png",
        "./icon.png",
        "assets//icon.png",
        "assets\\\\icon.png",
        "icon.svg",
        "icon",
    ):
        with pytest.raises(ValueError, match="PNG or JPEG"):
            validate_channel_icon_path(path, "icon")


def test_reads_an_image_whose_content_matches_its_type(root: Path) -> None:
    icon = load_channel_icon(root, "assets/icon.png")
    assert (icon.data, icon.content_type) == (PNG, "image/png")
    (root / "channels" / "icon.jpg").write_bytes(JPEG)
    assert load_channel_icon(root, "icon.jpg").content_type == "image/jpeg"


def test_refuses_files_missing_too_big_mislabeled_or_outside(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    with pytest.raises(FileNotFoundError):
        load_channel_icon(root, "missing.png")
    # A path names a file under channels/, not one at the project's root.
    (root / "top.png").write_bytes(PNG)
    with pytest.raises(FileNotFoundError):
        load_channel_icon(root, "top.png")
    (root / "channels" / "fake.png").write_text("<svg/>")
    with pytest.raises(ValueError, match="not a PNG"):
        load_channel_icon(root, "fake.png")
    (root / "channels" / "big.png").write_bytes(b"\0" * (CHANNEL_ICON_MAX_BYTES + 1))
    with pytest.raises(ValueError, match="at most"):
        load_channel_icon(root, "big.png")
    outside = tmp_path_factory.mktemp("outside") / "icon.png"
    outside.write_bytes(PNG)
    (root / "channels" / "linked.png").symlink_to(outside)
    with pytest.raises(ValueError, match="outside channels/"):
        load_channel_icon(root, "linked.png")


def _linear(icon: str) -> object:
    channel = channels.linear(app={"icon": icon})
    channel.name = "linear"
    return channel


def test_serves_the_icon_as_an_image_that_never_runs_as_a_page(root: Path) -> None:
    app = build_managed_http_app(channels=[_linear("assets/icon.png")], root=str(root))
    response = TestClient(app).get("/channels/linear/icon")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_leaves_out_an_icon_it_cant_serve(root: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    for app in (
        build_managed_http_app(channels=[_linear("assets/missing.png")], root=str(root)),
        build_managed_http_app(channels=[_linear("assets/icon.png")]),
    ):
        assert TestClient(app).get("/channels/linear/icon").status_code == 404
    assert sum("not serving" in record.getMessage() for record in caplog.records) == 2


def test_linear_takes_an_icon_under_channels() -> None:
    with pytest.raises(ValueError, match=r"app\.icon must be a path relative to channels/"):
        channels.linear(app={"icon": "../icon.png"})
    # Like Slack's and GitHub's, the app takes no icon URL.
    with pytest.raises(ValueError, match="unknown Linear app option icon_url"):
        channels.linear(app={"icon_url": "https://acme.dev/icon.png"})  # ty: ignore[invalid-argument-type]
    channel = channels.linear(app={"icon": "assets/icon.png"})
    assert channel.icon == "assets/icon.png"
    assert channel.provider_config["app"] == {"icon": "assets/icon.png"}
