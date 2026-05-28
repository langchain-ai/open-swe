"""Small text helpers."""


def truncate_with_ellipsis(text: str, max_len: int) -> str:
    """Truncate text to max_len characters, appending '...' if truncated.

    The returned string is guaranteed to be at most max_len characters long.
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
