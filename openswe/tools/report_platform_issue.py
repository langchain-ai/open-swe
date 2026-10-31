import logging
from dataclasses import dataclass, field
from uuid import uuid7

from pydantic import JsonValue, TypeAdapter

from openswe.memories.curator import WorkspaceCurator
from openswe.run_config import RunConfig
from openswe.threads.store import THREADS
from openswe.tools.mcp_exposure import expose_mcp
from openswe.utils.langsmith import create_langsmith_thread_feedback
from openswe.webhooks.event_log import EventLog, EventRefs
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)
_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_JSON = TypeAdapter(JsonValue)


@dataclass
class PlatformIssue:
    description: str
    keywords: list[str]
    report_id: str = field(default_factory=lambda: str(uuid7()))
    cfg: RunConfig | None = None
    details: dict[str, JsonValue] = field(default_factory=dict)
    public: bool = False

    @classmethod
    def redact(cls, value: JsonValue, key: str = "") -> JsonValue:
        normalized_key = key.lower().replace("-", "_")
        if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {item_key: cls.redact(item, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [cls.redact(item) for item in value]
        return value

    async def collect(self) -> None:
        try:
            self.cfg = RunConfig.from_runtime()
            self.details["configurable"] = self.redact(_JSON.validate_python(self.cfg.dump()))
            if self.cfg.thread_id:
                thread = await THREADS.get(self.cfg.thread_id)
                if thread is not None:
                    self.details["thread"] = self.redact(thread.model_dump(mode="json"))
                    self.public = thread.metadata.get("visibility") == "public"
        except Exception:
            logger.warning("Could not collect platform issue diagnostics", exc_info=True)

    async def record(self) -> bool:
        workspace_id = (
            await WORKSPACES.id_for_slug(self.cfg.workspace_slug)
            if self.cfg and self.cfg.workspace_slug
            else None
        )
        return await EventLog.append(
            "platform_issue",
            {
                "report_id": self.report_id,
                "thread_id": self.cfg.thread_id if self.cfg else None,
                "description": self.description,
                "keywords": list(self.keywords),
                "thread_details": self.details,
            },
            event_type="reported",
            delivery_id=self.report_id,
            refs=EventRefs(
                workspace_id=workspace_id,
                github_user_id=self.cfg.github_user_id or "" if self.cfg else "",
            ),
        )

    async def forward(self) -> str:
        if self.cfg is None or not self.cfg.workspace_slug:
            return "no_workspace"
        if not self.public:
            return "private_report_withheld"
        await WorkspaceCurator(self.cfg.workspace_slug).report_problem(
            report_id=self.report_id,
            description=self.description,
            keywords=self.keywords,
            thread_id=self.cfg.thread_id,
        )
        return "forwarded"

    async def export(self) -> bool:
        if self.cfg is None or not self.cfg.thread_id:
            return False
        return await create_langsmith_thread_feedback(
            self.cfg.thread_id,
            f"platform_issue:{self.report_id}",
            score=0.0,
            comment=self.description,
            source_info={
                "source": "report_platform_issue_tool",
                "report_id": self.report_id,
                "keywords": self.keywords,
            },
        )

    async def deliver(self) -> dict[str, str]:
        await self.collect()
        logger.warning(
            "Platform issue reported",
            extra={
                "platform_issue_report_id": self.report_id,
                "platform_issue_description": self.description,
                "platform_issue_keywords": self.keywords,
                "platform_issue_thread_details": self.details,
            },
        )
        result = {"report_id": self.report_id}
        try:
            result["event_log_status"] = "recorded" if await self.record() else "failed"
        except Exception:
            logger.exception("Could not store platform issue report")
            result["event_log_status"] = "failed"
        try:
            result["curator_status"] = await self.forward()
        except Exception:
            logger.exception("Could not forward platform issue report")
            result["curator_status"] = "failed"
        try:
            result["export_status"] = "exported" if await self.export() else "logged_only"
        except Exception:
            logger.exception("Could not export platform issue report")
            result["export_status"] = "logged_only"
        return result


@expose_mcp()
async def report_platform_issue(problem_description: str, keywords: list[str]) -> dict[str, str]:
    """Implement the `report_platform_issue` tool."""
    description = problem_description.strip()
    if not description:
        raise ValueError("Describe the platform issue in problem_description.")
    return await PlatformIssue(description, keywords).deliver()
