import hashlib
import uuid


def generate_thread_id_from_slack_thread(channel_id: str, thread_ts: str) -> str:
    """Generate a deterministic thread ID from a Slack thread identifier."""
    composite = f"{channel_id}:{thread_ts}"
    md5_hex = hashlib.md5(composite.encode("utf-8")).hexdigest()
    return str(uuid.UUID(hex=md5_hex))
