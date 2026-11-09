"""Task-aware workflow review presentation without changing push authorization."""

from collections.abc import Mapping
from dataclasses import dataclass

from openswe.tasks.store import TaskMembership
from openswe.threads.store import THREADS
from openswe.threads.workflow_approval import WORKFLOW_PUSH_APPROVALS_KEY


@dataclass(frozen=True)
class WorkflowReview:
    records: dict[str, dict[str, object]]

    @staticmethod
    def parse(metadata: Mapping[str, object]) -> dict[str, dict[str, object]]:
        raw = metadata.get(WORKFLOW_PUSH_APPROVALS_KEY)
        if not isinstance(raw, dict):
            return {}
        return {
            key: {**value, "fingerprint": key}
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    @classmethod
    async def resolve(
        cls,
        thread_id: str,
        metadata: Mapping[str, object],
        viewer_login: str | None,
        viewer_email: str | None = None,
    ) -> WorkflowReview:
        from openswe.threads.summary import thread_is_owner, thread_is_readable

        approvals = cls.parse(metadata)
        if not any(record.get("status") == "pending" for record in approvals.values()):
            return cls(approvals)
        context = await TaskMembership.context_for_thread(thread_id)
        if context is None or context.membership.role != "worker":
            return cls(approvals)
        coordinator_id = context.task.coordinator_thread_id
        if not coordinator_id or viewer_login is None:
            return cls(approvals)
        coordinator = await THREADS.get(coordinator_id)
        owner = metadata.get("owner_login")
        if (
            coordinator is None
            or not isinstance(owner, str)
            or not thread_is_owner(coordinator.metadata, owner)
            or not thread_is_readable(coordinator.metadata, viewer_login, viewer_email)
            or coordinator.metadata.get("workspace") != metadata.get("workspace")
        ):
            return cls(approvals)
        decisions = cls.parse(coordinator.metadata)
        for fingerprint, record in approvals.items():
            decision = decisions.get(fingerprint)
            if (
                record.get("status") == "pending"
                and decision is not None
                and decision.get("status") in ("approved", "rejected")
                and decision.get("repo") == record.get("repo")
                and decision.get("branch") == record.get("branch")
                and str(decision.get("decided_at", ""))
                >= str(record.get("first_requested_at") or record.get("requested_at", ""))
            ):
                approvals[fingerprint] = {
                    **record,
                    "coordinator_status": decision["status"],
                }
        return cls(approvals)

    @property
    def pending(self) -> bool:
        latest: dict[tuple[str, str], dict[str, object]] = {}
        for record in self.records.values():
            key = (str(record.get("repo", "")), str(record.get("branch", "")))
            current = latest.get(key)
            if current is None or str(record.get("requested_at", "")) > str(
                current.get("requested_at", "")
            ):
                latest[key] = record
        return any(record.get("status") == "pending" for record in latest.values())
