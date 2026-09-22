Record one point where the pull request's author steered Open SWE, and that changed what shipped.

Call this only after you have read the code. Guidance is about the change as a whole, not about a line in it: an instruction can land as a file that exists, an approach that is absent, a name used throughout, or a whole design rebuilt. What qualifies is that the pull request would look different if nobody had said it. What does not qualify is steering the session rather than the work — telling the agent to rebase, run a command, check a log or open a browser leaves nothing behind in the change.

Record at most $cap points, strongest first. Recording nothing is the right answer when the author only ever said "looks good".

Recording a point is not filing a finding. File a finding only when the final change contradicts what the author asked for, or applies it in one place and not another.

- `summary`: one line, past tense, naming what the author changed and what it changed it from. "Rejected the retry wrapper and asked for the error to propagate", never "The user gave feedback about retries".
- `quote`: the words from the author's message that carry the instruction, copied verbatim and unedited. Trim to the load-bearing sentence or two. A paraphrase loses the reader the link back to what was actually said.
