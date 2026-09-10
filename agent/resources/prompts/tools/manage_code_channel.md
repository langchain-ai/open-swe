Manage the complete Slack code-channel surface for this session.

Use `create` to promote the current Slack thread using its generated title.
`invite` names who else should be in that channel, as Slack user ids: whoever
the work is for, plus anyone they named. The person whose message opened the
channel is already in it. User ids appear in the conversation context (e.g.
@Name(U06KD8BFY95)). Use
`status`, `rename`, `context`, `summary`, `resource`, and `commands` for channel chrome. `view`
upserts an `html`, `diff`, `block_kit`, or `canvas` tab; HTML and diff content
can be passed directly or read from `file_path`, while Block Kit uses `blocks`
plus optional external-select `suggestions`, and canvas uses `canvas_id`. Use
`list_views` and `remove_view` to reconcile
tabs. Use `get_canvas` to read markdown and comments and `set_canvas` to
replace its markdown while preserving comment anchors. Post a closing summary
before `archive` and pass its timestamp as `summary_message_ts`.

Files must be inside the active sandbox work directory, valid UTF-8, and at
most 1 MB. Never publish secrets or credentials in a view.
