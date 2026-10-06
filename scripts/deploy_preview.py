# /// script
# requires-python = ">=3.14"
# dependencies = ["httpx>=0.28", "pydantic>=2.12"]
# ///
"""Wait for the automatic preview deployment and verify the published commit deploys."""

import asyncio
import os
import sys
import time
from typing import Literal

import httpx
from pydantic import BaseModel

POLL_SECONDS = 15
TIMEOUT_SECONDS = 60 * 60
LOG_LINES = 200

type RevisionStatus = Literal[
    "CREATING",
    "QUEUED",
    "AWAITING_BUILD",
    "BUILDING",
    "AWAITING_DEPLOY",
    "DEPLOYING",
    "CREATE_FAILED",
    "BUILD_FAILED",
    "DEPLOY_FAILED",
    "DEPLOYED",
    "SKIPPED",
    "INTERRUPTED",
    "UNKNOWN",
]

IN_PROGRESS: frozenset[RevisionStatus] = frozenset(
    {"CREATING", "QUEUED", "AWAITING_BUILD", "BUILDING", "AWAITING_DEPLOY", "DEPLOYING"}
)


class DeployError(Exception):
    """The preview revision did not reach DEPLOYED."""


class SourceRevisionConfig(BaseModel):
    repo_commit_sha: str | None = None


class Revision(BaseModel):
    id: str
    status: RevisionStatus
    status_message: str | None = None
    source_revision_config: SourceRevisionConfig | None = None

    @property
    def commit(self) -> str | None:
        return self.source_revision_config.repo_commit_sha if self.source_revision_config else None


class Revisions(BaseModel):
    resources: list[Revision]
    offset: int


class LogLine(BaseModel):
    message: str = ""
    timestamp: str | int | None = None
    level: str | None = None


class Logs(BaseModel):
    logs: list[LogLine] = []


class Deployer:
    def __init__(self, client: httpx.AsyncClient, deployment_id: str, expected_sha: str) -> None:
        self.client = client
        self.path = f"/v2/deployments/{deployment_id}"
        self.expected_sha = expected_sha
        self.deadline = time.monotonic() + TIMEOUT_SECONDS

    async def wait_for_revision(self) -> Revision:
        print(f"waiting for automatic deployment of {self.expected_sha}", flush=True)
        offset = 0
        while time.monotonic() < self.deadline:
            response = await self.client.get(
                f"{self.path}/revisions", params={"limit": 100, "offset": offset}
            )
            response.raise_for_status()
            page = Revisions.model_validate_json(response.content)
            for revision in page.resources:
                if revision.commit == self.expected_sha:
                    return revision
            if page.resources and page.offset > offset:
                offset = page.offset
            else:
                offset = 0
                await asyncio.sleep(POLL_SECONDS)
        raise DeployError(f"no automatic revision for {self.expected_sha} appeared before timeout")

    async def revision(self, revision_id: str) -> Revision:
        response = await self.client.get(f"{self.path}/revisions/{revision_id}")
        response.raise_for_status()
        return Revision.model_validate_json(response.content)

    async def settle(self, revision: Revision) -> Revision:
        status = revision.status
        print(f"revision {revision.id}: {status}", flush=True)
        while revision.status in IN_PROGRESS:
            if time.monotonic() > self.deadline:
                raise DeployError(f"revision {revision.id} still {revision.status} after timeout")
            await asyncio.sleep(POLL_SECONDS)
            revision = await self.revision(revision.id)
            if revision.status != status:
                status = revision.status
                print(f"revision {revision.id}: {status}", flush=True)
        return revision

    async def print_logs(self, revision: Revision) -> None:
        log_type = "build" if revision.status == "BUILD_FAILED" else "deploy"
        response = await self.client.get(
            f"{self.path}/revisions/{revision.id}/logs",
            params={"type": log_type, "order": "desc", "limit": LOG_LINES},
        )
        if response.is_error:
            print(f"could not fetch {log_type} logs: {response.status_code} {response.text}")
            return
        print(f"::group::last {LOG_LINES} {log_type} log lines")
        for line in reversed(Logs.model_validate_json(response.content).logs):
            print(
                " ".join(
                    str(part)
                    for part in (line.timestamp, line.level, line.message)
                    if part is not None and part != ""
                )
            )
        print("::endgroup::", flush=True)

    async def deploy(self) -> None:
        revision = await self.settle(await self.wait_for_revision())
        if revision.status != "DEPLOYED":
            await self.print_logs(revision)
            detail = f": {revision.status_message}" if revision.status_message else ""
            raise DeployError(f"revision {revision.id} ended {revision.status}{detail}")
        if revision.commit != self.expected_sha:
            raise DeployError(
                f"revision {revision.id} deployed {revision.commit or 'an unknown commit'}, "
                f"not the published {self.expected_sha}"
            )
        print(f"revision {revision.id} deployed {revision.commit}")


async def main() -> None:
    headers = {"X-Api-Key": os.environ["LANGSMITH_API_KEY"]}
    if workspace := os.environ.get("LANGSMITH_WORKSPACE_ID"):
        headers["X-Tenant-Id"] = workspace
    async with httpx.AsyncClient(
        base_url=os.environ.get("LANGSMITH_HOST_URL") or "https://api.host.langchain.com",
        headers=headers,
        timeout=30,
        transport=httpx.AsyncHTTPTransport(retries=3),
    ) as client:
        deployer = Deployer(client, os.environ["DEPLOYMENT_ID"], os.environ["EXPECTED_SHA"])
        await deployer.deploy()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except httpx.HTTPStatusError as exc:
        print(
            f"::error::{exc.request.method} {exc.request.url.path} returned {exc.response.status_code}: {exc.response.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    except (DeployError, httpx.HTTPError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(1)
