You are organizing a GitHub pull request's changed files into a clear, top-to-bottom walkthrough for a human reviewer.

Group the changed files by logical intent — put files that implement one coherent change together (e.g. "the new feature", "the supporting refactor", "the config wiring", "the tests"). Order the groups so a reviewer can read them top to bottom and build a mental model of the PR.

Rules:
- Assign every changed file to exactly one group.
- Use at most $max_groups groups. Prefer fewer, larger groups over many tiny ones.
- title: a short headline naming the change, roughly 4-10 words, no trailing punctuation. Wrap code identifiers (symbols, flags, file names) in `backticks`.
- summary: a short, plain explanation of what the group changes and why, that a reviewer can skim:
    - Keep it focused — a few short sentences. Do not restate the diff line by line.
    - Wrap every code identifier, symbol, type, flag, and path in `backticks`.
    - Plain prose only — no code blocks and no links.
- files: the exact file paths (copied verbatim from the list below) in this group, in the order a reviewer should read them.

Changed files:
$file_list

Diffs:
$diffs
