# /// script
# requires-python = ">=3.14"
# dependencies = ["httpx>=0.28", "pydantic>=2.12"]
# ///
"""Roll the preview deployment to the published preview branch and fail unless it deploys."""

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

    async def latest(self) -> Revision | None:
        response = await self.client.get(f"{self.path}/revisions", params={"limit": 1})
        response.raise_for_status()
        resources = Revisions.model_validate_json(response.content).resources
        return resources[0] if resources else None

    async def revision(self, revision_id: str) -> Revision:
        response = await self.client.get(f"{self.path}/revisions/{revision_id}")
        response.raise_for_status()
        return Revision.model_validate_json(response.content)

    async def create(self) -> Revision:
        body = {"source_revision_config": {"langgraph_config_path": "langgraph.json"}}
        response = await self.client.post(f"{self.path}/revisions", json=body)
        if response.status_code == 409:
            busy = await self.latest()
            if busy is None:
                raise DeployError(f"revision creation conflicted: {response.text}")
            print(
                f"revision {busy.id} is still rolling out; waiting before creating ours", flush=True
            )
            await self.settle(busy)
            response = await self.client.post(f"{self.path}/revisions", json=body)
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
        revision = await self.settle(await self.create())
        if revision.status != "DEPLOYED":
            await self.print_logs(revision)
            detail = f": {revision.status_message}" if revision.status_message else ""
            raise DeployError(f"revision {revision.id} ended {revision.status}{detail}")
        if revision.commit and revision.commit != self.expected_sha:
            print(
                f"::warning::deployed {revision.commit[:7]}, not the published "
                f"{self.expected_sha[:7]}; the preview branch moved during the build"
            )
        print(f"revision {revision.id} deployed {revision.commit or 'an unknown commit'}")


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
