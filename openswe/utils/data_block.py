"""Untrusted text placed inside the XML-style wrappers of a model prompt."""

import re

# XML tolerates whitespace around the tag name (e.g. `</body >`, `</ body\n>`), so a
# literal `.replace()` of the canonical spelling alone is insufficient.
_WRAPPER_TAGS = (
    "pr_review_threads",
    "thread",
    "comment",
    "body",
    "pr_overview",
    "title",
    "review_walkthrough",
    "step",
    "summary",
    "files",
)
_CLOSING_TAG_RE = re.compile(r"</\s*(" + "|".join(_WRAPPER_TAGS) + r")\s*>", re.IGNORECASE)


def escape_for_data_block(text: str) -> str:
    """Neutralize closing tags so an attacker-controlled body can't break out.

    Each wrapper's end tag is rewritten to an inert ``</name_>`` form that stays
    human-readable but is no longer a valid closer.
    """
    return _CLOSING_TAG_RE.sub(lambda m: f"</{m.group(1).lower()}_>", text)
