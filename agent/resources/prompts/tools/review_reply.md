Post a message to the reader in the review channel.

`message` is a Jinja template rendered against the checkout before it is posted. Quote code only through its helpers, never by retyping it: `{{ staged() }}` for the chunk on screen as a diff, `{{ stat() }}` for its diffstat, `{{ diff("path") }}` for what is still unreviewed in a file, and `{{ code("path", start, end) }}` for lines of a file at the pull request head. The rest is Slack Markdown.

- `chunk`: `true` when this message shows the staged chunk for approval. The message must include `{{ staged() }}` or `{{ stat() }}`, and a "Looks good" button is added. Stage the chunk first; the call fails when nothing is staged.
- `options`: extra buttons, such as `["Approve on GitHub"]` on the closing message. A click arrives as the reader's next message.

After a chunk message, end your turn and wait for the reader.
