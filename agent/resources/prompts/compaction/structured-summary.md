Respond ONLY with the extracted context. Do not include any additional information, or text before or after the extracted context.

<media_reference_information>
Conversation history may include XML media reference tags, for example:
<image url="/conversation_history/media/{{hash}}.png" />
These tags mean the original message included media that was preserved at the referenced backend path.
Treat the tag and path as part of the conversation context. Do not infer visual details that are not available from surrounding text.
When the media could be important for future context, preserve the media reference in your summary.
The model consuming the summary can call `read_file` on the referenced path if it needs to inspect the media.
</media_reference_information>

Write the summary with exactly these sections, in this order. Write `None` when a
section has nothing to report. Preserve file paths, commands, error messages, and
decisions verbatim when they matter for continuing the task. Do not invent details
that are not present in the messages.

## Objective

The user's request and any constraints or acceptance criteria stated so far.

## Completed work

What has been done and verified, including the tests or commands that proved it.

## Files and code changes

Every file read or modified, with the relevant function, class, or line region and
a short description of the change. Mark files that were only inspected.

## Commands and results

Commands run, exit status, and the outcomes that matter: failing test ids,
regressions, install errors, environment quirks.

## Errors and open questions

Unresolved failures, hypotheses already ruled out, and questions that still need
an answer from the user or from the repository.

## Next steps

The immediate next actions, in order, and why each one is needed.

<messages>
Messages to summarize:
{messages}
</messages>
