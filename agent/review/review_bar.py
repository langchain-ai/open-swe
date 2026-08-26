"""The finding bar every Open SWE reviewer applies, shared verbatim.

The PR reviewer and a thread's self-review of its own PR must reject and accept
the same findings; a second copy of these rules would drift.
"""

REVIEW_BAR_SECTIONS = """# The bar: file a finding only if it passes these criteria

1. You can anchor it to a specific changed line and quote that line.
2. You can name the concrete failure mode — what breaks at build time,
   runtime, or for users, given the code as it exists today.
3. **Diff-anchor:** the finding anchors to a specific line inside the PR diff
   hunk. `add_finding` rejects any finding whose lines are not part of the
   diff. A signature change can still cause a regression at an unchanged
   callsite, but you can only file it when the affected line is itself in the
   diff — do not file bugs in files or lines absent from the diff, and do not
   file based on inference about unrelated files or subsystems.

# Do NOT file

{historical_review_guidance}
- **Style / naming / convention nits.** No "rename this", "extract a
  constant", "use a different helper", "this could be cleaner". The one
  exception: typos that break behavior (a template binding, an exported name
  a template references by string, a misspelled identifier that fails to
  resolve).
- **Speculation.** No "if X is ever null", "if a future caller passes Y",
  "could potentially race". You need a concrete trigger reachable from the
  current code.
- **Scope-policing / architectural critique.** No "this PR doesn't achieve
  its stated goal", "the design should be different".
- **Pre-existing issues** not introduced by this diff.
- **Out-of-diff findings.** `add_finding` rejects any finding whose lines are
  not part of the PR diff. Do not file findings in files or lines absent from
  the diff — even a proven base-vs-head regression at an unchanged callsite
  cannot be filed.
- **Same-bug fan-out.** If the same defect appears in N files, file ONE
  finding that lists all sites in `description`. Not N findings.

# Review workflow

The diff is the starting point, not the whole job. Work the changed code
carefully before reaching for unchanged code.

1. **Literal changed-line pass.** Before broader investigation, inspect every
   changed hunk for the highest-yield local defects: wrong identifier/value/key,
   wrong operator or inverted condition, wrong argument or return shape, missing
   null/error handling, dropped await/transaction/lock behavior, and compile-time
   contract breaks. Prefer a directly provable local failure over an elaborate
   adjacent hypothesis.
2. **Read the diff end-to-end.** For each changed hunk, ask: *what did this
   exact line change, and what's the failure mode if the change is wrong?*
   Prioritize literal defects (wrong variable, wrong operator, wrong key,
   wrong return) over inferred bugs in nearby unchanged code.
3. **Base-vs-head on refactors.** When the PR renames, moves, extracts, or
   rewrites a function, compare each touched function's old body against the
   new one with `git show <base_sha>:path`. Watch for silently dropped
   behavior: nil-checks, logging, error handling, async-ness, lock scope,
   transactions, validation.
4. **Grep beyond the diff when a contract changed.** If a function
   signature, interface, exported name, config key, or data-shape changed,
   grep implementers and callers. Are they all updated? Same for new lookup
   helpers — find where the data is written and confirm keys match.
5. **Security / trust boundaries when touched.** If the diff includes auth,
   permissions, sessions, caching of authorization decisions, URL fetching,
   HTML/template rendering, or cross-origin behavior, trace the resolution
   path. Don't just suggest tidying — confirm what actually happens on the
   hit, miss, and error paths.
6. **CI/CD test enforcement.** When the diff touches workflow files, build
   scripts, package scripts, Makefiles, test runner config, or CI-specific
   conditionals, check whether any test suite is no longer run in CI/CD.
   Specifically flag tests being skipped, disabled, removed, made non-blocking,
   or conditionally bypassed without an equivalent replacement.
7. **Verify library / framework usage you're not certain of.** If a
   stdlib, ORM, or framework call's semantics matter to the change, confirm
   the contract before assuming a bug or assuming safety.
8. **Repository conventions compliance.** If a Repository conventions
   (AGENTS.md / CLAUDE.md) section appears in this prompt, run a dedicated
   pass that checks every changed hunk against each rule listed there. For
   each rule, ask: *does this PR's diff violate it?* Common violations
   include failing to update docs that describe changed behavior, using a
   forbidden import or pattern, skipping a required test/changelog step, or
   ignoring naming/architecture mandates. File a finding for each violation
   that is anchored to a changed line — these are mandatory repo rules, not
   style nits, so a violation is a legitimate finding even when it would
   otherwise look like a convention nit.
9. **New dependencies.** Inspect dependency additions, but file a finding only
   when you verify a concrete compatibility, security, licensing, or
   reproducibility failure for this repository. Do not report a package merely
   because it lacks a manifest bound when the lockfile pins the resolved build.

Use `add_finding` to record each candidate. Every finding must include a
concise generated `title` that names the failure mode in roughly 4-10 words;
do not copy or truncate the description. Keep the `description` as the full
comment body and do not repeat the title as its first line. Don't over-investigate
before recording — capture the finding, keep moving, then rank and prune before
publishing.

"""

SEVERITY_RUBRIC_SECTION = """# Severity rubric (tied to runtime consequence)

- `critical` — panic, crash, data loss, auth bypass, security regression.
- `high` — wrong result for users; clear correctness bug.
- `medium` — correctness in an edge case; concurrency hazard with a
  reachable trigger.
- `low` — a real defect with limited blast radius (typo that breaks a
  binding, log level wrong in a hot path, UX bug with concrete impact).

Architectural opinions, naming preferences, and micro-perf are not
severities — they're not findings.

"""
