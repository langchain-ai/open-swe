Activate plan mode mid-run.

Call this when the user asks you to plan work (e.g. "plan xxx") or explicitly
requests plan mode. Do not enter it based solely on task complexity, size,
ambiguity, or an incidental mention of the word "plan".

Once activated, stay read-only for the target repo: research the codebase,
create or edit a dated, self-contained HTML artifact outside any repo (for
example, ``/workspace/plans/YYYY-MM-DD-short-task-slug.html``), then publish
it with the ``save_plan`` tool and share the plan-review link with the user. Do not
edit repo files, commit, push, or open a PR — the user reviews the plan and
approves it before you implement.
