"""The cache key the workspace list is served from.

Its own module so :mod:`agent.workspaces.store`, which writes workspaces, and
:mod:`agent.workspaces.routing`, which reads them while resolving a workspace,
can agree on one key without importing each other.
"""

WORKSPACE_LIST_CACHE_KEY = "workspaces:all"
# Routing resolves a tag or a user's default against this list; short enough
# that an admin's edit takes effect while they are still looking at the page.
WORKSPACE_LIST_CACHE_TTL_SECONDS = 30.0
