Activate plan mode mid-run.

Call this when you believe the task would benefit from a structured
implementation plan before writing any code — e.g. when the request is
complex, touches many files, or has multiple valid approaches. This is
NOT triggered by the word "plan" appearing in the request; use your
judgment about whether planning is genuinely warranted.

Once activated, stay read-only for the target repo: research the codebase,
create or edit a dated, self-contained HTML artifact outside any repo (for
example, ``/workspace/plans/YYYY-MM-DD-short-task-slug.html``), then publish
it with the ``save_plan`` tool and share the plan-review link with the user. Do not
edit repo files, commit, push, or open a PR — the user reviews the plan and
approves it before you implement.
