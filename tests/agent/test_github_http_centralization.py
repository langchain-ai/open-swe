"""Pin GitHub HTTP centralisation so it cannot silently regress.

``agent/utils/github_http.py`` exists so every GitHub call shares one set of
timeouts, retries, rate-limit handling and media types. A module that spells the
API host or the ``vnd.github+json`` media type itself has, by definition, opted
out of that. These tests pin the exact set of modules allowed to spell either,
so a new hand-rolled client fails here instead of shipping.
"""

from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent"

GITHUB_API_HOST = "api.github.com"
GITHUB_JSON_MEDIA_TYPE = "application/vnd.github+json"

# The only module that may name the GitHub API host, plus two documented
# exceptions that are not API calls / not this package's to change.
_HOST_ALLOWED = {
    # Builds every GitHub URL, via github_url().
    "utils/github_http.py",
    # A sandbox-proxy host match rule, not an API call.
    "integrations/langsmith.py",
    # Already routes through github_http's client and retries; it only spells
    # the URL. Owned by a parallel refactor, so left for that package to fold
    # into github_url().
    "review/diff.py",
}

# The media type is constructed in exactly one place; there is no exception.
_MEDIA_TYPE_ALLOWED = {"utils/github_http.py"}


def _modules_containing(literal: str) -> set[str]:
    return {
        str(path.relative_to(AGENT_ROOT))
        for path in AGENT_ROOT.rglob("*.py")
        if literal in path.read_text(encoding="utf-8")
    }


def test_only_github_http_names_the_github_api_host() -> None:
    assert _modules_containing(GITHUB_API_HOST) == _HOST_ALLOWED


def test_only_github_http_builds_the_github_json_media_type() -> None:
    assert _modules_containing(GITHUB_JSON_MEDIA_TYPE) == _MEDIA_TYPE_ALLOWED
