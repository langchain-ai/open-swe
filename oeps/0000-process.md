# OEP-0000: OEP purpose and process

- **Authors:** Open SWE maintainers
- **Status:** Active
- **Created:** 2026-09-03
- **Discussion:** https://github.com/langchain-ai/open-swe/pulls?q=is%3Apr+OEP
- **Supersedes:** None

## Summary

Open SWE Enhancement Proposals (OEPs) record consequential product, architecture, security,
and project-process decisions in the repository. The process is intentionally lighter than the
systems that inspired it: one directory, one template, one canonical discussion per proposal, and
one explicit decision recorded in git.

## When to write an OEP

Use an OEP when a change:

- establishes or changes a product or security philosophy;
- changes a public interface or an important compatibility promise;
- crosses several components or integrations;
- introduces a hard-to-reverse architectural direction; or
- needs a durable decision before implementation begins.

Routine features, bug fixes, refactors, and implementation details do not need an OEP. When in
doubt, start with an issue or discussion; maintainers can ask for an OEP if the decision needs a
durable record.

## Process

1. **Discuss the idea.** Start in an issue, discussion, or another public project forum. Confirm
   that the problem is worth solving and broad enough to need an OEP.
2. **Open a proposal PR.** Copy [`TEMPLATE.md`](TEMPLATE.md), choose the next unused four-digit
   number, and add `NNNN-short-title.md` with status `Draft`. Link the canonical discussion. Number
   collisions are resolved during review; numbers do not imply priority.
3. **Merge the draft.** Maintainers review the document for clarity, scope, and completeness.
   Merging publishes the draft on `main`; it does **not** accept the proposal. Discussion should
   continue in the linked public forum so feedback is not split across closed PRs and private
   conversations.
4. **Resolve it.** The author incorporates material feedback. Maintainers responsible for the
   affected area determine rough consensus and open or approve a small PR changing the status to
   `Accepted`, `Rejected`, or `Withdrawn`, adding a dated resolution and its rationale. A proposal
   without sufficient consensus remains a draft or is rejected; acceptance is never implied by
   inactivity.
5. **Implement separately.** Implementation PRs link the accepted OEP. An accepted OEP records
   direction, not a guarantee that every implementation detail or the implementation itself will
   ship.

Maintainers may fast-track an uncontroversial or urgent decision, but the proposal and resolution
must still be recorded on `main`. Anyone may author an OEP; no editor, sponsor, scheduled meeting,
or separate repository is required.

## Statuses

| Status | Meaning |
|---|---|
| `Draft` | Published for discussion; no decision has been made. |
| `Active` | A process OEP that remains in force and may evolve. |
| `Accepted` | The direction is approved; implementation may proceed. |
| `Rejected` | The proposal was considered and declined. |
| `Withdrawn` | The authors no longer propose it. |
| `Superseded` | A later OEP replaces this decision. |

Accepted, rejected, withdrawn, and superseded OEPs are historical records. Substantive changes
require a new OEP; corrections and links may be added in place. Active process OEPs may evolve
through normal pull requests.

## Required content

Keep an OEP as short as the decision allows. Every OEP includes:

- number, title, authors, status, creation date, and discussion link;
- a summary and motivation;
- the proposed decision, including scope and non-goals;
- security and privacy implications;
- meaningful alternatives and unresolved questions; and
- after a decision, a dated resolution with rationale.

Delete empty optional sections rather than filling them with boilerplate. Diagrams, prototypes,
and implementation links are welcome when they clarify the decision.

## Prior art

The process borrows selectively from established systems:

- [Go proposals](https://github.com/golang/proposal/blob/master/README.md) begin with lightweight
  discussion and require a design document only when needed. OEPs retain that proportionality but
  keep the durable proposal in the Open SWE repository.
- [Python PEPs](https://peps.python.org/pep-0001/) separate publishing a draft from accepting it,
  preserve rejected decisions, and record a canonical discussion and resolution. OEPs keep those
  durable-history properties without editors, delegates, or a separate rendering system.
- [Rust RFCs](https://rust-lang.github.io/rfcs/0002-rfc-process.html) use Markdown pull requests to
  build rough consensus before implementation. OEPs use the same familiar contribution path, but
  draft proposals are merged to `main` before resolution so work-in-progress decisions remain
  discoverable.
- [Kubernetes KEPs](https://github.com/kubernetes/enhancements/blob/master/keps/sig-architecture/0000-kep-process/README.md)
  emphasize cross-project coordination, risks, and durable project knowledge. Their release and
  production-readiness machinery is intentionally omitted here.
- [Django DEPs](https://github.com/django/deps/blob/main/final/0001-dep-process.rst) adapt PEPs to a
  GitHub-native project workflow. OEPs likewise favor ordinary Markdown and pull requests, while
  omitting formal teams, shepherds, and status directories.

## Security and privacy

OEPs are public documents. They must describe security consequences without containing credentials,
private incident details, personal data, or other secrets. Security-sensitive supporting material
should use the project's private security-reporting path and be summarized safely in the OEP.

## Resolution

OEP-0000 is active as the initial OEP process. Changes to the process use ordinary pull-request
review and remain visible in this file's git history.
