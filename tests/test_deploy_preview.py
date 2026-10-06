import time
from collections import deque

import httpx
import pytest

from scripts import deploy_preview
from scripts.deploy_preview import Deployer, DeployError, Revision, Revisions, SourceRevisionConfig


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
