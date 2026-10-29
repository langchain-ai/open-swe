"""Compose the canary controller from trusted configuration and a backend dispatch gateway."""

import hashlib
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from pydantic import SecretStr

from openswe.config import ENV
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.repositories import Repository
from openswe.jobs import queue as jobs
from openswe.merge_queue import runtime
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.dispatch import DispatchGateway
from openswe.merge_queue.native import NativeQueueRuntime
from openswe.merge_queue.store import MergeQueue
from openswe.merge_queue.validation_contract import WORKFLOW

logger = logging.getLogger(__name__)


class QueueController:
    def __init__(self, config: QueueConfiguration):
        self.config = config
        self.dispatcher = (
            DispatchGateway(
                str(config.dispatch_url), SecretStr(ENV.MERGE_QUEUE_DISPATCH_SECRET.require())
            )
            if config.mode == "custom" or config.check_app_id is not None
            else None
        )

    @asynccontextmanager
    async def open_repo(self, queue: MergeQueue) -> AsyncIterator[RepoClient]:
        if (
            queue.workspace_id != self.config.workspace_id
            or queue.target_branch != "main"
            or queue.mode != self.config.mode
        ):
            raise ValueError("Queue does not belong to this controller")
        repository = await Repository.get(self.config.repository)
        if repository is None or repository.id != queue.repository_id:
            raise ValueError("Queue repository binding changed")
        owner, name = self.config.repository.split("/", 1)
        permissions = (
            {"contents": "read", "metadata": "read", "pull_requests": "read"}
            if self.config.mode == "native"
            else {
                "actions": "read",
                "checks": "read",
                "contents": "write",
                "metadata": "read",
                "pull_requests": "read",
                "statuses": "read",
            }
        )
        if self.config.mode == "native" and self.dispatcher is not None:
            permissions.update({"actions": "read", "checks": "read"})
        async with GitHubClient.as_app(owner, name, permissions=permissions) as github:
            repo = github.repo(*self.config.repository.split("/", 1))
            if (await repo.info()).get("id") != self.config.repository_id:
                raise ValueError("Queue GitHub repository identity changed")
            if self.config.mode == "custom" and self.dispatcher is not None:
                revision = await repo.branch_sha("main")
                workflow = await repo.raw_file(f".github/workflows/{WORKFLOW}", revision)
                if (
                    workflow is None
                    or hashlib.sha256(workflow).hexdigest() != self.config.workflow_digest
                ):
                    raise ValueError("Queue workflow changed")
            yield repo

    async def reconcile(self, payload: runtime.QueueJob) -> None:
        if payload.queue_id != self.queue.id or payload.workspace_id != self.config.workspace_id:
            return
        try:
            config = await QueueConfiguration.load(payload.workspace_id)
        except ValueError:
            logger.exception(
                "Merge queue configuration is invalid", extra={"queue_id": str(self.queue.id)}
            )
            config = None
        if config is not None and (
            config.repository.lower() != self.config.repository.lower()
            or config.repository_id != self.config.repository_id
            or config.mode != self.config.mode
        ):
            logger.error(
                "Merge queue identity changed; explicit handoff required",
                extra={"queue_id": str(self.queue.id)},
            )
            config = None
        current = QueueController(config) if config is not None else self
        await NativeQueueRuntime(
            self.queue.id,
            self.config.repository_id,
            current.open_repo,
            config=config,
            dispatcher=current.dispatcher if config is not None else None,
        ).reconcile(payload)

    async def start(self) -> None:
        repository = await Repository.get(self.config.repository)
        if repository is None:
            raise ValueError("Onboard the queue repository into its workspace first")
        queue = await MergeQueue.register(self.config.workspace_id, repository.id, "main")
        self.queue = queue
        if self.config.mode == "native":
            await queue.enable_native_observation()
            controller = self
        else:
            if queue.mode != "custom":
                raise ValueError("Cannot restart a custom controller for a native queue")
            assert self.dispatcher is not None
            controller = runtime.QueueRuntime(
                {queue.id: self.config.policy},
                self.open_repo,
                self.dispatcher,
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


async def wake_native_queue(payload: Mapping[str, object]) -> bool:
    controller = CONTROLLER
    if controller is None or controller.config.mode != "native":
        return False
    config = await QueueConfiguration.load(controller.config.workspace_id)
    if config is None or config.mode != "native":
        return False
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return False
    full_name = repository.get("full_name")
    if (
        repository.get("id") != config.repository_id
        or not isinstance(full_name, str)
        or full_name.lower() != config.repository.lower()
    ):
        return False
    queue = controller.queue
    # Events only wake a live read; old payloads never overwrite the observed queue.
    await jobs.schedule(
        runtime.JOB_KIND,
        runtime.QueueJob(queue_id=queue.id, workspace_id=queue.workspace_id).model_dump(
            mode="json"
        ),
        key=f"merge-queue-native-wakeup:{queue.id}",
        replace=False,
    )
    return True


async def start_queue() -> None:
    global CONTROLLER
    CONTROLLER = None
    runtime.RUNTIME = None
    config = await QueueConfiguration.load()
    if config is None:
        return
    controller = QueueController(config)
    await controller.start()
    CONTROLLER = controller
