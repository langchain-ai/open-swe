Record one point where the pull request's author steered Open SWE, and that changed the code that shipped.

Call this only after you have read the code. Guidance is about the change as a whole, not about a line in it: an instruction can land as a file that exists, an approach that is absent, a name used throughout, or a whole design rebuilt. What qualifies is that the diff would look different if nobody had said it. What does not qualify:

- Steering the session rather than the work: telling the agent to rebase, run a command, check a log or open a browser leaves nothing behind in the change.
- Editing the pull request's title or description. Prose about the change is not the change.
- A message whose instruction you cannot read, such as one that only links to a review comment or asks to "address the feedback". If you cannot say what the author asked for, skip it.

Record at most $cap points, strongest first. Recording nothing is the right answer when the author only ever said "looks good". The reviewer reads every point you record and checks that the final change carries it through everywhere, so record an instruction even when it looks only partly applied.

- `summary`: a headline a reviewer can skim, at most 100 characters. Past tense, naming what the author asked for and what it replaced: "Dropped the retry wrapper so errors propagate", never "The user gave feedback about retries". Write about the code, in plain words. Never include identifiers (comment or review IDs, URLs, commit SHAs, thread IDs), and never comment on what you could or could not trace.
- `quote`: the words from the author's message that carry the instruction, copied verbatim and unedited. Trim to the load-bearing sentence or two. A paraphrase loses the reader the link back to what was actually said.
