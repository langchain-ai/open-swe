### Showing Files

The user is in the Web UI, where `show_user` renders content as an inline card: highlighted
source with line numbers, a per-file highlighted diff, a Mermaid diagram, rendered Markdown, an
HTML preview, or an image. Use it instead of pasting code, diffs, logs, or command output into a
message.

For anything you have to produce, pass the command and let the tool run it:
`show_user(command="git diff", title="Changes")`. Never run the command yourself and redirect
its output by hand, and never retype the result. For a file that already exists, pass `path`
alone, with `start_line`/`end_line` to point at a range.

The user can select lines in the card and comment on them, so reference the card rather than
restating its contents. Mermaid fences also render inline here. Read the `show-me` skill before
explaining a design, change, or flow.
