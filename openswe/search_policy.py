"""Tool policy for agents that search repository content through the shell."""

SHELL_SEARCH_EXCLUDED_TOOLS = frozenset({"grep", "glob"})
