Record one piece of human input that shaped the code this pull request shipped. The opening request counts as human input, as does every follow-up.

Call this only after you have read the code. Input is about the change as a whole, not about a line in it: an instruction can land as a file that exists, an approach that is absent, a name used throughout, or a whole design rebuilt. What qualifies is that the diff would look different if nobody had said it. What does not qualify:

- Directing the session rather than the work: telling the agent to rebase, run a command, check a log or open a browser leaves nothing behind in the change.
- Editing the pull request's title or description. Prose about the change is not the change.
- A message whose instruction you cannot read, such as one that only links to a review comment or asks to "address the feedback". If you cannot say what the person asked for, skip it.

Record at most $cap points, strongest first. When the opening request is the only input that shaped the code, record it as the one point and say plainly that nothing after it changed the code. The reviewer reads every point you record and checks that the final change carries it through everywhere, so record an instruction even when it looks only partly applied.

- `summary`: one sentence a reviewer can skim, at most 200 characters. Past tense, naming what the person asked for and, for a follow-up, what it replaced: "Asked to drop the retry wrapper so errors propagate", never "The user gave feedback about retries". Write about the code, in plain words. Be honest about what the input did, but never include identifiers (comment or review IDs, URLs, commit SHAs, thread IDs).
- `quote`: the words from the message that carry the instruction, copied verbatim and unedited. Trim to the load-bearing sentence or two. A paraphrase loses the reader the link back to what was actually said.
