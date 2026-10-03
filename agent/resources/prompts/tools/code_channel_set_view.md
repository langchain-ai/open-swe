Create or replace a view (a tab) in this conversation's Slack code channel, through Slack's `agents.conversations.setView`. It works only when this conversation is a code channel. Use it for anything worth a tab of its own: a diff, an HTML page, Block Kit or a canvas.

- `view_type`: `diff`, `html`, `block_kit` or `canvas`.
- `view_key`: names the view, so setting the same key again replaces it. Required for every type but `diff`, where it is optional.
- `name`: the tab's label.
- `content`: the view's body for `diff` (a unified diff) and `html`, at most 1 MB. Or pass `file_path` instead: a file in the sandbox under the work directory, such as a diff you wrote with `git diff … > /path`.
- `blocks`: the Block Kit blocks for `block_kit`.
- `canvas_id` and `access_level` (`read`, `write` or `comment`): for `canvas`.
- `base_branch`, `head_branch`: the branch labels shown on a `diff` view.
- `csp`: for `html`, the extra domains the page may use, as `resource_domains` and `connect_domains` lists of at most 20 each.
- `agent_content_hash`: passed through to Slack as is.

Returns Slack's response, including the view's id.
