"""User MCP tools keep a recognisable name and a stable, collision-free suffix."""

import re

from agent.tool_loaders.mcp import group_name, prefixed_tool_name

_VALID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def test_tool_name_keeps_the_tool_readable_within_provider_limits():
    name = prefixed_tool_name("GitHub Enterprise Tools", "search_repositories", scope="a" * 32)
    assert _VALID.match(name)
    assert name.startswith("mcp_GitHub_Enterprise_Tools_search_repositories_")
    long = prefixed_tool_name("x" * 100, "list_pull_request_review_comments_for_a_file", scope="s")
    assert _VALID.match(long)
    assert "_list_pull_request_review_comment" in long


def test_same_tool_on_two_connections_gets_distinct_names():
    first = prefixed_tool_name("Notion", "search", scope="a" * 32)
    second = prefixed_tool_name("Notion", "search", scope="b" * 32)
    assert first != second
    assert first == prefixed_tool_name("Notion", "search", scope="a" * 32)


def test_group_name_prefers_the_connection_name():
    assert group_name("Notion", "a" * 32, set()) == "Notion"
    assert group_name("Notion", "b" * 32, {"Notion"}) == "Notion (bbbbbbbb)"
    assert group_name("Browser", "c" * 32, {"Browser"}) == "Browser (cccccccc)"
