import json
from collections.abc import Callable

import pytest

from openswe.dashboard.settings import (
    AppRelease,
    SettingsUpdate,
    get_settings,
    upsert_instance_settings,
)
from openswe.plugins.records import AppVersion, PluginApps
from openswe.tools.manage_review_apps import manage_review_apps
from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def private_admin(grant_tool_access: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    grant_tool_access(admin=True, admin_surface=True)
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


async def _release(name: str, *, review: bool, browser: bool = False) -> AppVersion:
    owner = await User.sign_in("github", "1", login="ada")
    manifest = {
        "name": name,
        "displayName": name,
        "version": "1",
        "sidebar": {"label": name},
        "permissions": ["storage"],
        "collections": {"captures": {"write": "app"}},
        **(
            {"review": {"instructions": "Capture.", "writes": ["captures"], "browser": browser}}
            if review
            else {}
        ),
    }
    html = f'<script type="application/openswe-plugin+json">{json.dumps(manifest)}</script>'
    return await PluginApps.publish(owner.id, html, origin={})


def _pinned(release: AppVersion) -> AppRelease:
    return AppRelease(app=str(release.app), release=str(release.id))


def _described(release: AppVersion) -> dict[str, object]:
    """How ``read`` shows an app pinned to ``release``, less its browser and newer release."""
    return {
        "id": str(release.app),
        "release": str(release.id),
        "name": release.manifest.name,
        "release_number": release.number,
        "review_step": True,
        "writes": ["captures"],
    }


async def test_instance_review_apps_are_added_and_removed_keeping_other_settings() -> None:
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Keep me"))
    release = await _release("design-qa", review=True)

    added = await manage_review_apps("add", str(release.app))

    assert added["apps"] == [
        {**_described(release), "browser": False, "newer_release_number": None}
    ]
    assert (await get_settings())["org_guidelines"] == "Keep me"
    await manage_review_apps("remove", str(release.app))
    assert (await manage_review_apps("read"))["apps"] == []


async def test_reviews_follow_the_release_an_admin_added_until_they_add_it_again() -> None:
    first = await _release("design-qa", review=True)
    await manage_review_apps("add", str(first.app))
    # The owner's next release asks for a browser; no admin has seen it.
    second = await _release("design-qa", review=True, browser=True)

    read = (await manage_review_apps("read"))["apps"]
    assert read == [
        {
            **_described(first),
            "browser": False,
            "newer_release_number": second.number,
        }
    ]

    updated = (await manage_review_apps("add", str(first.app)))["apps"]
    assert updated == [{**_described(second), "browser": True, "newer_release_number": None}]


async def test_preview_checks_are_set_and_read_keeping_other_settings() -> None:
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Keep me"))

    saved = await manage_review_apps(
        "set_preview_checks",
        preview_checks=[" Build and Deploy Frontend Preview ", ".github/workflows/preview.yml"],
    )

    expected = [".github/workflows/preview.yml", "Build and Deploy Frontend Preview"]
    assert saved["preview_checks"] == expected
    assert (await manage_review_apps("read"))["preview_checks"] == expected
    assert (await get_settings())["org_guidelines"] == "Keep me"
    with pytest.raises(ValueError, match="set_preview_checks"):
        await manage_review_apps("read", preview_checks=[])


async def test_only_an_app_with_a_review_step_can_be_added() -> None:
    release = await _release("docs", review=False)

    with pytest.raises(ValueError, match="review step"):
        await manage_review_apps("add", str(release.app))


async def test_adding_an_app_keeps_previously_approved_releases() -> None:
    first, second = await _release("design-qa", review=True), await _release("audit", review=True)
    await upsert_instance_settings(
        SettingsUpdate(review_apps=[_pinned(first)], org_guidelines="Keep me")
    )

    await manage_review_apps("add", str(second.app))

    assert (await get_settings())["review_apps"] == [_pinned(first), _pinned(second)]
    assert (await get_settings())["org_guidelines"] == "Keep me"
