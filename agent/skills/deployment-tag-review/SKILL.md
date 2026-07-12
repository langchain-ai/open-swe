---
name: deployment-tag-review
description: Fast-path review for PRs whose diff is solely container image tag/digest/version bumps in deployment manifests (e.g. langchain-ai/deployments YAML), with no code or logic change. Verify the new tag/digest is applied consistently across every changed file, publish, and STOP. Use this instead of the full code-review workflow so the reviewer does not re-derive base-vs-head, grep-beyond-diff, and git-history investigation on trivial config PRs.
---

# Deployment image-tag consistency review

This skill is a scoped fast-path for a recurring class of trivial PR: a pure
deployment image-tag bump. The whole job is confirming one tag/digest change is
applied consistently across the changed manifests — nothing more.

## When to activate

Activate ONLY when the PR diff consists solely of container image
tag/digest/version bumps in deployment manifests (e.g. `langchain-ai/deployments`
YAML files) — an image reference changed from one tag/digest to another, with no
code or logic change anywhere in the diff. If any hunk touches code, logic,
config semantics beyond the image reference, or a non-manifest file, fall back to
the full review workflow in the system prompt.

## Steps

1. **Enumerate changed files from the diff only.** Work from the PR diff. Do not
   clone-walk or list the tree beyond the changed files.
2. **Extract the old and new tag/digest from each changed hunk.** For each changed
   file, read the `-` and `+` image reference lines and record the old and new
   tag/digest/version.
3. **Confirm consistency across all changed files.** The new tag/digest must be
   applied identically in every changed file. If any changed file was left on the
   old tag/digest (or bumped to a different value), file that inconsistency as a
   finding anchored to the offending changed line.
4. **Publish and STOP.** If every changed file carries the same new tag/digest,
   publish the review with 0 findings and stop.

## Do NOT, within this workflow

The changed files are the whole job. None of the following is needed to verify
tag consistency, and they are the source of the observed token blowup (a 35x
spread — 165K vs 5.8M tokens — for the same class of trivial PR):

- No base-vs-head comparison (`git show <sha>:path`).
- No `git blame`.
- No `git log` / history walking on unrelated commits or fields.
- No full `gh pr view` metadata fetches.

Verify consistency across the changed files, publish, and stop.
