"""The cache key the workspace list is served from.

Its own module so :mod:`agent.workspaces.store`, which writes workspaces, and
:mod:`agent.workspaces.routing`, which reads them on every webhook delivery, can
agree on one key without importing each other.
"""

WORKSPACE_LIST_CACHE_KEY = "workspaces:all"
# Webhooks route on every delivery, so the list is cached; short enough that an
# admin's edit takes effect while they are still looking at the page.
WORKSPACE_LIST_CACHE_TTL_SECONDS = 30.0
