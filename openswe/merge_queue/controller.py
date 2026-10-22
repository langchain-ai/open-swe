"""Compose the canary controller from trusted configuration and an isolated App."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import SecretStr

from openswe.config import ENV
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.queue_app import QueueApp
from openswe.github.repositories import Repository
from openswe.jobs import queue as jobs
from openswe.merge_queue import runtime
from openswe.merge_queue.configuration import QueueConfiguration, load_configuration
from openswe.merge_queue.store import MergeQueue
from openswe.merge_queue.validation_contract import WORKFLOW


class QueueController:
    def __init__(self, config: QueueConfiguration):
        self.config = config
        assert config.policy.validation is not None
        self.app = QueueApp(
            config.app_id,
            config.installation_id,
            config.repository_id,
            config.repository,
            config.policy.validation.dispatch_actor_id,
            SecretStr(ENV.MERGE_QUEUE_APP_PRIVATE_KEY.require()),
        )

    @asynccontextmanager
    async def open_repo(self, queue: MergeQueue) -> AsyncIterator[RepoClient]:
        if queue.workspace_id != self.config.workspace_id or queue.target_branch != "main":
            raise ValueError("Queue does not belong to this controller")
        repository = await Repository.get(self.config.repository)
        if repository is None or repository.id != queue.repository_id:
            raise ValueError("Queue repository binding changed")
        token = await self.app.token()
        async with GitHubClient.connect(token=token) as github:
            repo = github.repo(*self.config.repository.split("/", 1))
            if (await repo.info()).get("id") != self.config.repository_id:
                raise ValueError("Queue GitHub repository identity changed")
            revision = await repo.branch_sha("main")
            workflow = await repo.raw_file(f".github/workflows/{WORKFLOW}", revision)
            if (
                workflow is None
                or hashlib.sha256(workflow).hexdigest() != self.config.workflow_digest
                or await repo.queue_dispatch_actor() != self.app.actor_id
            ):
                raise ValueError("Queue workflow or reserved dispatch actor changed")
            yield repo

    async def start(self) -> None:
        repository = await Repository.get(self.config.repository)
        if repository is None:
            raise ValueError("Onboard the queue repository into its workspace first")
        queue = await MergeQueue.register(self.config.workspace_id, repository.id, "main")
        self.queue = queue
        controller = runtime.QueueRuntime(
            {queue.id: self.config.policy},
            self.open_repo,
            retention_days=self.config.retention_days,
        )
        await jobs.schedule(
            runtime.JOB_KIND,
            runtime.QueueJob(queue_id=queue.id, workspace_id=queue.workspace_id).model_dump(
                mode="json"
            ),
            cron="* * * * *",
            key=f"merge-queue:{queue.id}",
            replace=False,
        )
        runtime.RUNTIME = controller


CONTROLLER: QueueController | None = None


async def start_queue() -> None:
    global CONTROLLER
    config = load_configuration()
    if config is None:
        CONTROLLER = None
        runtime.RUNTIME = None
        return
    controller = QueueController(config)
    await controller.start()
    CONTROLLER = controller
