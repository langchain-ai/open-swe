List every workspace with its snapshot state.

``default`` is the workspace runs fall back to; the rest are drafts until a
repo, Slack channel, tag, or user default routes a run to them.

Returns:
    ``{"ok": True, "environments": [...]}``.
