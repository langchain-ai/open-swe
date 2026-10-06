import time
from collections import deque
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts import deploy_preview
from scripts.deploy_preview import Deployer, DeployError, Revision, Revisions, SourceRevisionConfig


@pytest.fixture(autouse=True)
def preview_branch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    branch = AsyncMock(return_value="published")
    monkeypatch.setattr(deploy_preview, "preview_sha", branch)
    monkeypatch.setattr(deploy_preview, "POLL_SECONDS", 0)
    return branch


async def test_waits_for_published_commit_without_triggering_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy_preview, "POLL_SECONDS", 0)
    expected = Revision(
        id="expected",
        status="BUILDING",
        source_revision_config=SourceRevisionConfig(repo_commit_sha="published"),
    )
    other = expected.model_copy(
        update={
            "id": "other",
            "source_revision_config": SourceRevisionConfig(repo_commit_sha="other"),
        }
    )
    responses = deque(
        [
            ("0", Revisions(resources=[other], offset=1)),
            ("1", Revisions(resources=[], offset=1)),
            ("0", Revisions(resources=[other], offset=1)),
            ("1", Revisions(resources=[expected], offset=2)),
        ]
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/revisions"):
            offset, page = responses.popleft()
            assert request.url.params["offset"] == offset
            return httpx.Response(200, json=page.model_dump())
        assert request.url.path.endswith("/revisions/expected")
        return httpx.Response(
            200, json=expected.model_copy(update={"status": "DEPLOYED"}).model_dump()
        )

    async with httpx.AsyncClient(
        base_url="https://deploy.test", transport=httpx.MockTransport(respond)
    ) as client:
        await Deployer(client, "preview", "published").deploy()
    assert not responses


@pytest.mark.parametrize(
    ("status", "commit", "error"),
    [
        ("BUILD_FAILED", "published", "ended BUILD_FAILED"),
        ("INTERRUPTED", "published", "ended INTERRUPTED"),
        ("SKIPPED", "published", "ended SKIPPED"),
        ("DEPLOYED", "other", "not the published published"),
        ("DEPLOYED", None, "an unknown commit"),
    ],
)
async def test_rejects_failed_or_wrong_commit(
    status: str, commit: str | None, error: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_preview, "POLL_SECONDS", 0)

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"logs": []})
        if request.url.path.endswith("/revisions"):
            return httpx.Response(
                200,
                json={
                    "resources": [
                        {
                            "id": "expected",
                            "status": "BUILDING",
                            "source_revision_config": {"repo_commit_sha": "published"},
                        }
                    ],
                    "offset": 1,
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "expected",
                "status": status,
                "source_revision_config": {"repo_commit_sha": commit},
            },
        )

    async with httpx.AsyncClient(
        base_url="https://deploy.test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(DeployError, match=error):
            await Deployer(client, "preview", "published").deploy()


@pytest.mark.parametrize("status", [None, "BUILDING", "INTERRUPTED", "SKIPPED"])
async def test_stops_when_preview_branch_is_superseded(
    status: str | None, preview_branch: AsyncMock, capsys: pytest.CaptureFixture[str]
) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/revisions")
        preview_branch.return_value = "replacement"
        resources = (
            [
                {
                    "id": "expected",
                    "status": status,
                    "source_revision_config": {"repo_commit_sha": "published"},
                }
            ]
            if status
            else []
        )
        return httpx.Response(200, json={"resources": resources, "offset": 0})

    async with httpx.AsyncClient(
        base_url="https://deploy.test", transport=httpx.MockTransport(respond)
    ) as client:
        await Deployer(client, "preview", "published").deploy()
    output = capsys.readouterr().out
    assert "::warning::preview branch moved from published to replacement" in output
    assert "replacement deployment not verified" in output


async def test_branch_lookup_failure_is_not_treated_as_superseded(
    preview_branch: AsyncMock,
) -> None:
    preview_branch.side_effect = DeployError("could not read the preview branch")
    async with httpx.AsyncClient(base_url="https://deploy.test") as client:
        with pytest.raises(DeployError, match="could not read the preview branch"):
            await Deployer(client, "preview", "published").deploy()


async def test_times_out_when_automatic_revision_never_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy_preview, "POLL_SECONDS", 0)

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        deployer.deadline = time.monotonic() - 1
        return httpx.Response(200, json={"resources": [], "offset": 0})

    async with httpx.AsyncClient(
        base_url="https://deploy.test", transport=httpx.MockTransport(respond)
    ) as client:
        deployer = Deployer(client, "preview", "published")
        with pytest.raises(DeployError, match="no automatic revision for published"):
            await deployer.deploy()
