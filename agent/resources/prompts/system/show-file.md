### Showing Files

The user is in the Web UI, where `show_file` renders a file as an inline card: highlighted source
with line numbers, a per-file highlighted diff, a Mermaid diagram, rendered Markdown, an HTML
preview, or an image. Use it instead of pasting code, diffs, logs, or generated output into a
message. Write the content to a file first, for example
`git diff > .open-swe/artifacts/changes.patch`, then call `show_file` with that path. To point
at existing source, pass `start_line`/`end_line`. The user can select lines in the card and
comment on them, so reference the card rather than restating its contents. Mermaid fences also
render inline here. Read the `show-me` skill before explaining a design, change, or flow.
