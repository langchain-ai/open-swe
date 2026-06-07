"""Input processing for greeting requests."""



def sanitize_input(message: str | None) -> str | None:
    """Return a stripped, lowercased version of the message, or None for empty/whitespace-only."""
    if message is None:
        return None
    if not isinstance(message, str):
        return None
    cleaned = message.strip()
    if not cleaned:
        return None
    return cleaned.lower()
