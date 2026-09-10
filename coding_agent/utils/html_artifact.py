"""Shared HTML skeleton for the agent's published artifacts."""

import base64
import html
import re
import shlex
import textwrap

_FULL_DOCUMENT_PATTERN = r"<html([\s>]|$)"
_FULL_DOCUMENT = re.compile(_FULL_DOCUMENT_PATTERN, re.IGNORECASE | re.MULTILINE)
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


def sandbox_wrap_command(
    source: str, destination: str, *, limit: int, title: str | None = None
) -> str:
    """Shell command that applies this module's wrapping to a file in the sandbox.

    Runs the same detection as `wrap_html_artifact` under the sandbox's python3
    rather than reimplementing it in shell, and prints the byte size it wrote.
    """
    prefix, suffix = artifact_skeleton(title)
    script = textwrap.dedent(
        f"""
        import re, sys

        source, destination, limit, prefix, suffix = sys.argv[1:6]
        with open(source, "rb") as handle:
            data = handle.read(int(limit))
        full = re.search(
            {_FULL_DOCUMENT_PATTERN!r},
            data.decode("utf-8", "replace"),
            re.IGNORECASE | re.MULTILINE,
        )
        payload = data if full else prefix.encode() + data + suffix.encode()
        with open(destination, "wb") as handle:
            handle.write(payload)
        print(len(payload))
        """
    ).strip()
    args = " ".join(shlex.quote(arg) for arg in (source, destination, str(limit), prefix, suffix))
    encoded = base64.b64encode(script.encode()).decode()
    return f"printf %s {shlex.quote(encoded)} | base64 -d | python3 - {args}"


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
