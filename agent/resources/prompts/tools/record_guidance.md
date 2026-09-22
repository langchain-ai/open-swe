Record one point where the pull request's author steered Open SWE, and that you can see in the final change.

Call this only after you have looked at the code. A point qualifies when you can name a file this PR changed where the author's instruction is visible — the thing they asked for is there, the thing they ruled out is gone, or the approach they redirected to is the one that shipped. If you cannot locate it in the change, do not record it: the author telling the agent to rebase, to run a command, or to open a browser steered the session, not the pull request.

Record at most $cap points, strongest first. Do not file a finding for a point you record here; this is a summary for the reader, not a defect.

- `summary`: one line, past tense, naming what the author changed and what it changed it from. "Rejected the retry wrapper and asked for the error to propagate", never "The user gave feedback about retries".
- `quote`: the words from the author's message that carry the instruction, copied verbatim. Trim to the load-bearing sentence or two.
- `kind`: `correction` when they said something already built was wrong, `constraint` when they ruled an approach, file or dependency out, `direction` when they chose a different approach from the one in progress, `preference` when they stated a durable taste about how the code should look.
- `file`: the path in this PR where the steering is visible.
- `start_line`: the line in that file, when one line carries it.
