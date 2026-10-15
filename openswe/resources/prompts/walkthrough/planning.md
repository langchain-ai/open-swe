# Planning the walkthrough

The walkthrough is a plan of steps called chunks that a reader goes through one at a time, top to bottom, approving each one, then Other last: the changes not worth reading, shown as a short summary. The plan is shared: the review page, the reviewer and every person walked through this pull request in Slack read the same one, and other planners may be adding to it while you work. Every planning tool's result shows the plan as it stands: its chunks, how much is in Other, and the hunks still unplanned. Only unplanned hunks can be placed, unless you replace their existing chunks.

The unit of the plan is the hunk. Hunks start out as `git diff` prints them with its default context (never `-U0`, which splits them differently), and `walkthrough_split_hunks` splits one into smaller hunks at changed lines you name. A hunk is named by its file and its first changed line: `+12` for added head line 12, `-40` for deleted merge-base line 40. The status lists each file's unplanned hunks as `path +12 (+3/-1), -40 (-5)`. Placing a hunk takes all of its unplanned lines, and every hunk belongs to exactly one chunk or to Other.

Target a maximum of 10 steps for the whole walkthrough, including Other, regardless of pull request size. This is a ceiling, not a minimum or a goal to fill: fewer steps are usually better for small pull requests. Use only as many steps as the change needs. Budget the chunks before placing hunks, accounting for chunks already in the shared plan. For large pull requests, group changes into broader concepts rather than adding steps; the step budget takes priority over keeping each chunk short. Never meet the budget by hiding real behavior changes in Other.

A chunk is one coherent concept, told through the hunks that make it up, wherever they are: a new type and the code that reads it, a function and its callers, a behavior and the test that pins it. Gather related hunks from different files into one chunk instead of giving each hunk or each file its own. When git's hunk spans different chunks, or mixes imports with a real change, split it so each part lands in the chunk, or Other, it belongs to. Split between whole statements and blocks, never line by line: every chunk should read as contiguous code with its closing braces, not scattered single lines. Order the chunks so each builds on what the reader already saw: usually the core idea or data model first, then the logic that uses it, then wiring, with related tests alongside the behavior they cover.

Send to Other the hunks that hold nothing but:

1. Imports, includes, requires and `use` declarations.
2. Boilerplate and ceremony: migration revision headers and `downgrade` stubs, module docstrings that restate the code, `__all__`, registration lists, `__init__` re-exports.
3. Generated code and artifacts: lockfiles, snapshots, golden files, bundles, vendored code, files marked "generated, do not edit".
4. Pure formatting and reordered but unchanged declarations.
5. Mechanical repetition: for a rename or call-site migration, put one representative hunk in a chunk and leave the rest.
6. Forced plumbing: forwarded parameters, zero values for a new return slot, annotations that follow from a change elsewhere.
7. Comment churn that narrates the code, and reworded error or log text.
8. Test scaffolding and repeated cases, keeping one scenario per behavior.
9. Docs, changelogs and config churn that restate the code.

A hunk that mixes noise with a real change goes in a chunk, unless you split the noise off first. Never hide a real behavior change in Other: a new condition, a different call, a changed value, return path or data flow always goes in a chunk.

- `walkthrough_plan_chunk` places one chunk of hunks with a title and explanation, and can send hunks to Other in the same step. Use `replace` with existing chunk numbers to revise a step or merge several into one, especially when approaching the step budget. Include all hunks you want to keep in the replacement; omitted hunks return to unplanned. The code is rendered from its hunks, so never paste code into the explanation. The explanation is two to four plain sentences: what the chunk changes, why, how its hunks fit together, and anything worth a second look. Wrap identifiers, types, flags and paths in `backticks`.
- `walkthrough_split_hunks` splits unplanned hunks at changed lines you name, before you place their parts.
- `walkthrough_move_to_other` sends hunks to Other without placing a chunk, or with `restore: true` takes them out again.
- `walkthrough_describe_other` sets the one or two sentences readers see above Other, on what kinds of change it holds. Update it when Other changes in kind.

When the pull request moved since the plan was made, chunks kept every line that survived by content, and a new line in a hunk a chunk already holds joined that chunk. Only hunks no chunk holds are unplanned. Place those with `after` so each lands next to the chunk it belongs with, rather than at the end.
