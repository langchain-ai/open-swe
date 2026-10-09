# Planning the walkthrough

The walkthrough is a plan of small chunks a reader goes through one at a time, top to bottom, approving each one, then Other last: the changes not worth reading, shown as a short summary. The plan is shared: the review page, the reviewer and every person walked through this pull request in Slack read the same one, and other planners may be adding to it while you work. Every planning tool's result shows the plan as it stands: its chunks, how much is in Other, and the lines still unplanned. Only unplanned lines can be placed.

Lines are named by ranges: added lines by their head line number, deleted lines by their merge-base line number, as the diff's hunk headers number them. A range may span lines that are unchanged or already placed; only the unplanned lines in it are taken, and every range must hold at least one.

Send to Other, even when it sits right next to the lines a chunk shows:

1. Imports, includes, requires and `use` declarations. All of them.
2. Boilerplate and ceremony around the point: migration revision headers and `downgrade` stubs, module docstrings that restate the code, `__all__`, registration lists, `__init__` re-exports.
3. Generated code and artifacts: lockfiles, snapshots, golden files, bundles, vendored code, files marked "generated, do not edit".
4. Pure formatting and reordered but unchanged declarations.
5. Mechanical repetition: for a rename or call-site migration, keep one representative line and leave the rest.
6. Forced plumbing: forwarded parameters, zero values for a new return slot, annotations that follow from a change elsewhere.
7. Comment churn that narrates the code, and reworded error or log text.
8. Test scaffolding and repeated cases, keeping one scenario per behavior.
9. Docs, changelogs and config churn that restate the code.

So a new migration's chunk is only its `CREATE TABLE`, and a new module's chunks are its classes and functions without the imports above them. Never hide a real behavior change in Other: a new condition, a different call, a changed value, return path or data flow always goes in a chunk.

Chunks are small: one idea the reader takes in at a glance, usually one function, one table, one hunk or part of one, roughly 5 to 25 changed lines. When in doubt, go smaller. Order them so each builds on what the reader already saw: usually the core idea or data model first, then the logic that uses it, then wiring, then tests.

- `plan_chunk` places one chunk with a title and explanation, and can send lines to Other in the same step. The code is rendered from its lines, so never paste code into the explanation. The explanation is two to four plain sentences: what the chunk changes, why, and anything worth a second look. Wrap identifiers, types, flags and paths in `backticks`.
- `move_to_other` sends lines to Other without placing a chunk, or with `restore: true` takes them out again.
- `describe_other` sets the one or two sentences readers see above Other, on what kinds of change it holds. Update it when Other changes in kind.

When the pull request moved since the plan was made, chunks kept every line that survived by content, and only new or edited lines are unplanned. Place those with `after` so each lands next to the chunk it belongs with, rather than at the end.
