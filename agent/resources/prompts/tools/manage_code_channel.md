Manage the complete Slack code-channel surface for this session.

`create` opens a code channel and starts a **separate session** in it, which
takes the task over from here. That session begins with no history and its own
fresh sandbox, so `instructions` must describe the task on its own: what to do,
what has been decided, and anything learned so far that it would otherwise have
to rediscover. Commit and push any local work first — create a branch for it if
it is still on the default branch — because the new sandbox is a clean checkout
and cannot see this one's working tree, so anything unpushed is lost to it.
After it succeeds, tell the user which channel is handling the task and stop
working on it here.

`invite` is who starts out in that channel, as Slack user ids, and it needs at
least one person — a channel nobody is in is a channel nobody reads. Include
whoever asked for the work, plus anyone they named or anyone already taking
part in this conversation. User ids appear in the conversation context (e.g.
@Name(U06KD8BFY95)).

Use `status`, `rename`, `context`, `summary`, `resource`, and `commands` for channel chrome. `view`
upserts an `html`, `diff`, `block_kit`, or `canvas` tab; HTML and diff content
can be passed directly or read from `file_path`, while Block Kit uses `blocks`
plus optional external-select `suggestions`, and canvas uses `canvas_id`. Use
`list_views` and `remove_view` to reconcile
tabs. Use `get_canvas` to read markdown and comments and `set_canvas` to
replace its markdown while preserving comment anchors. Post a closing summary
before `archive` and pass its timestamp as `summary_message_ts`.

Files must be inside the active sandbox work directory, valid UTF-8, and at
most 1 MB. Never publish secrets or credentials in a view.
