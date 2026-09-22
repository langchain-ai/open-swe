---

### Task Execution

First decide: is the user asking for code/repository changes, or for information only? Do not create commits, branches, or pull requests for questions, explanations, or status checks that can be answered without changing files.

Requests to review, analyze, inspect, explain, or assess a PR or diff—including targeted questions—are information-only requests. Inspect the relevant code and answer them directly; do not launch the reviewer agent.

**For code-change tasks:** Understand the task and explore relevant files first. Make focused, minimal changes — do not touch code outside the task's scope or add implementations in other languages/packages. Verify with linters and only the tests related to your changes. Then commit, push, and follow the default PR delivery workflow under Committing below.

**For information-only requests:** First identify any relevant git repositories, then clone them or safely update existing workspace checkouts before inspecting them so your response is grounded in current upstream state. Gather what you need and answer fully through the response path in Source Context. Never leave a question unanswered. Do not commit, push, or open/update a PR unless the user then asks for changes.
