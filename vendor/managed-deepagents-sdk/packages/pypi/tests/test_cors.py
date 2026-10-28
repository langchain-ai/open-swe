"""CORS allowlist helpers for the managed HTTP app."""

from __future__ import annotations

import re

import pytest
from starlette.applications import Starlette

from managed_deepagents._cors import (
    _cors_origin_regex,
    is_managed_cors_path,
    match_cors_origin,
    resolve_cors_allowlist,
    wrap_with_managed_cors,
)


def test_is_managed_cors_path_matches_prefixes_only() -> None:
    assert not is_managed_cors_path("/identity/slack/status")
    assert is_managed_cors_path("/connectors/demo/x")
    assert is_managed_cors_path("/channels/slack/events")
    assert not is_managed_cors_path("/ok")
    assert not is_managed_cors_path("/threads/search")


def test_resolve_cors_allowlist_strips_trailing_slash() -> None:
    assert resolve_cors_allowlist(
        {"MDA_PUBLIC_APP_URL": "https://mda-slack-example.pages.dev/"}
    ) == ["https://mda-slack-example.pages.dev"]


def test_resolve_cors_allowlist_merges_extra_origins() -> None:
    assert resolve_cors_allowlist(
        {
            "MDA_PUBLIC_APP_URL": "https://app.example.com",
            "MDA_CORS_ORIGINS": "http://localhost:4900, https://other.example.com/",
        }
    ) == [
        "http://localhost:4900",
        "https://app.example.com",
        "https://other.example.com",
    ]


def test_match_cors_origin_allows_pages_preview() -> None:
    allowlist = ["https://mda-slack-example.pages.dev"]
    assert (
        match_cors_origin("https://mda-slack-example.pages.dev", allowlist)
        == "https://mda-slack-example.pages.dev"
    )
    assert (
        match_cors_origin("https://2fbb7491.mda-slack-example.pages.dev", allowlist)
        == "https://2fbb7491.mda-slack-example.pages.dev"
    )
    # Deeper nesting is rejected (credentialed CORS must not trust arbitrary depth).
    assert (
        match_cors_origin(
            "https://a.b.mda-slack-example.pages.dev",
            allowlist,
        )
        is None
    )
    assert match_cors_origin("https://evil.example.com", allowlist) is None
    # Scheme must match: credentialed CORS must not reflect http for https.
    assert match_cors_origin("http://mda-slack-example.pages.dev", allowlist) is None
    assert match_cors_origin("http://2fbb7491.mda-slack-example.pages.dev", allowlist) is None


def test_match_cors_origin_allows_explicit_http_allowlist_entry() -> None:
    assert (
        match_cors_origin("http://localhost:4900", ["http://localhost:4900"])
        == "http://localhost:4900"
    )


def test_cors_origin_regex_matches_single_subdomain_label() -> None:
    allowlist = ["https://mda-slack-example.pages.dev"]
    pattern = _cors_origin_regex(allowlist)
    assert pattern is not None
    compiled = re.compile(f"^(?:{pattern})$")
    assert compiled.match("https://mda-slack-example.pages.dev")
    assert compiled.match("https://2fbb7491.mda-slack-example.pages.dev")
    assert compiled.match("https://a.b.mda-slack-example.pages.dev") is None
    assert compiled.match("https://evil.example.com") is None
    assert compiled.match("http://mda-slack-example.pages.dev") is None
    assert compiled.match("http://2fbb7491.mda-slack-example.pages.dev") is None


def test_wrap_with_managed_cors_preserves_starlette_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """langgraph_api rejects custom apps that are not Starlette/FastAPI."""
    monkeypatch.setenv("MDA_PUBLIC_APP_URL", "https://app.example.com")
    app = Starlette(routes=[])
    wrapped = wrap_with_managed_cors(app)
    assert isinstance(wrapped, Starlette)
    assert wrapped is app
