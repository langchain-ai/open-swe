import secrets
import time
import uuid


def _uuid7() -> str:
    timestamp_ms = (time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    uuid_int = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=uuid_int))


async def report_platform_issue() -> dict[str, str]:
    """Report an issue with the sandbox or execution environment."""
    return {"report_id": _uuid7()}
