"""Shared HTML skeleton for the agent's published artifacts."""

import html
import re

_FULL_DOCUMENT = re.compile(r"<html[\s>]", re.IGNORECASE)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)

RESET = (
    "*,*::before,*::after{box-sizing:border-box}"
    "body{margin:0}"
    "img,svg,video{max-width:100%;height:auto}"
)

DEFAULT_TITLE = "Artifact"


def is_full_document(content: str) -> bool:
    return bool(_FULL_DOCUMENT.search(content))


def artifact_skeleton(title: str | None = None) -> tuple[str, str]:
    """Return the ``(prefix, suffix)`` a bare fragment is wrapped in."""
    resolved = (title or "").strip() or DEFAULT_TITLE
    prefix = (
        "<!doctype html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(resolved)}</title>\n"
        f"<style>{RESET}</style>\n"
        "</head>\n<body>\n"
    )
    return prefix, "\n</body>\n</html>\n"


def wrap_html_artifact(content: str, *, title: str | None = None) -> str:
    """Wrap fragment ``content`` in the artifact skeleton, or return it untouched.

    A fragment's own ``<title>`` is lifted into the generated ``<head>``, since a
    title element left in the body is ignored by browsers.
    """
    if is_full_document(content):
        return content
    found = _TITLE.search(content)
    if found:
        content = _TITLE.sub("", content, count=1).strip()
        title = html.unescape(found.group(1)).strip() or title
    prefix, suffix = artifact_skeleton(title)
    return f"{prefix}{content}{suffix}"
