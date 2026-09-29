Post a message to the reader in the review channel, such as a greeting or an answer to a question.

`message` is a Jinja template rendered against the checkout before it is posted. Quote code only through its helpers, never by retyping it: `{{ chunk() }}` for the chunk on screen, `{{ diff("path") }}` for a file's whole diff, and `{{ code("path", start, end) }}` for lines of a file at the pull request head. The rest is Slack Markdown.

- `options`: buttons to add. A click arrives as the reader's next message.

Never use it to show a chunk or Other; `show_chunk` and `show_other` do that.
