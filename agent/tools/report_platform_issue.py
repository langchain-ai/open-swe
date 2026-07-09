import logging
import secrets
import time
import uuid

logger = logging.getLogger(__name__)


def _uuid7() -> str:
    timestamp_ms = (time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    uuid_int = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=uuid_int))


async def report_platform_issue(
    summary: str | None = None,
    hint: str | None = None,
) -> dict[str, str]:
    """Report an issue with the sandbox, execution environment, or a failed delivery.

    Pass ``summary`` (and the failing tool's ``hint``) when a final delivery
    could not reach the user so the completed work's outcome is surfaced.
    """
    report_id = _uuid7()
    report: dict[str, str] = {"report_id": report_id}
    if summary:
        report["summary"] = summary
    if hint:
        report["hint"] = hint
    if summary or hint:
        logger.warning("report_platform_issue %s: summary=%r hint=%r", report_id, summary, hint)
    return report
