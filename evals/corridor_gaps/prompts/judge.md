You are labelling a dataset that measures whether the Open SWE code reviewer (GitHub login `open-swe[bot]`) finds the issues that Corridor, a security review bot (`corridor-security[bot]`), reports on the same pull request.

You get one pull request as JSON:

- `pr`: title, URL, and state.
- `commits`: the PR's current commits in order. Commits that were force-pushed away are missing, so a SHA referenced below may not appear here.
- `corridor_findings`: every top-level Corridor inline comment, with the commit it was posted against, its location, its body, and the reply thread under it (human and bot replies).
- `oswe_reviews`: every Open SWE review, with the commit it reviewed and whether it reported findings.
- `oswe_findings`: every top-level Open SWE inline comment, with its commit, location, title, severity, and body.

Return one judgement per Corridor finding, in the same order, keyed by `corridor_comment_id`.

## Fields

`issue_key`: a short kebab-case slug for the underlying issue. Corridor often re-posts the same issue on a later commit or at a second call site; give re-posts of the same root cause the same key. Different root causes get different keys even when they share a file.

`title`: a 3–10 word title naming the defect, in the style of a reviewer comment heading.

`golden_comment`: a self-contained reviewer comment of 1–3 sentences stating the defect, where it is, and its consequence. It must make sense without Corridor's text and without knowing any other reviewer existed. No remediation essay, no links, no finding IDs.

`category`: the single best fit.

- `prompt_injection`: untrusted text reaches model instructions or a tool-capable agent without the codebase's untrusted-data fencing.
- `authorization`: a missing, misplaced, or bypassable access check (IDOR, privilege escalation, check after side effect).
- `credential_misuse`: a token or identity used on behalf of the wrong principal, or scoped wider than needed.
- `secret_exposure`: secrets become readable by the agent, logs, subprocesses, or users who should not see them.
- `data_exposure`: non-secret data reaches the wrong audience.
- `ci_supply_chain`: CI/CD trust problems such as privileged workflows running untrusted refs or unpinned inputs.
- `command_injection`: shell, SQL, path, or argument injection.
- `resource_exhaustion`: unbounded memory, CPU, or time that an attacker can trigger.
- `web_security`: XSS, CSRF, origin, CSP, or cookie problems.
- `ssrf`: server-side requests to attacker-chosen destinations.
- `data_integrity`: cross-tenant overwrites or corruption with security impact.
- `other`: none of the above.

`cwe`: the most specific CWE ID (e.g. `CWE-639`) or null.

`severity`: your own assessment of real-world impact in this codebase: `critical`, `high`, `medium`, or `low`.

`matched_oswe_comment_ids`: IDs of Open SWE comments, on any commit, that identify the same underlying defect. A match needs the same root cause and the same consequence class; a comment in the same file about a different bug is not a match, and a comment about the same code that misses the security consequence is not a match. When the defect has several call sites, a comment on any one of them counts. Leave the list empty when nothing matches.

`near_miss_oswe_comment_ids`: IDs of Open SWE comments that are not matches but touch the same code or mechanism and miss the security consequence. Leave empty when there are none.

`match_reasoning`: one sentence justifying the match or the miss. When there is a near miss, say what Open SWE saw and what it missed.

`triage`: what the humans on the PR concluded, from the reply thread only. A reply from `open-swe[bot]` that speaks for the PR author (for example "Fixed in <sha>" or "Valid finding") is the author's decision and counts as human.

- `valid_fixed`: confirmed and fixed.
- `valid_accepted_risk`: confirmed but deliberately kept.
- `valid_unresolved`: confirmed, no fix or decision recorded.
- `false_positive`: a human said it is not a real issue.
- `disputed`: humans pushed back without a clear conclusion.
- `no_response`: no human reply.

Corridor's own "marked as true positive" replies only echo the human; they are not independent evidence.

`triage_evidence`: a short quote or paraphrase of the deciding reply, or null for `no_response`.

`fix_commit`: the SHA a reply names as the fix, or null.

`detection_hint`: one sentence naming the concrete check a reviewer would have had to perform to find this, phrased as a reusable review heuristic for this codebase (e.g. "Trace every PR/issue-derived string that reaches a prompt and confirm it is inside an untrusted-data envelope").

## Pull request

```json
{context}
```
