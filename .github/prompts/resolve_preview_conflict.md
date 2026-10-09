This checkout is the preview deployment being assembled: `main` plus every labelled pull request that merged cleanly. The pull requests listed below (`#<number> <head sha> <title>`) conflicted with it. Merge them on top of `HEAD`, resolving only their conflicts so every side behaves as intended. Leave unrelated typecheck and lint fixes to the separate fix-up step after these merges.

Finish with `cli_result`. A script parses `stdout`, so it must follow this format exactly:

```
merged: <numbers>
#<number>: <reason>
```

- The first line is `merged:` followed by the numbers of every listed pull request now in the preview, each preceded by a single space, without `#` (`merged: 12 34`). If none went in, the line is just `merged:`.
- Then one `#<number>: <reason>` line for each listed pull request you left out, with a one-sentence reason. Nothing for the ones you merged.
- No other lines, no Markdown, no code fences.

`exit_code` is 0 if every listed pull request is merged, 1 otherwise.
