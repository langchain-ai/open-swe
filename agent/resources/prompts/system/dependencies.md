---

### Dependencies

Install dependencies only if the task requires it, using the project's package manager; skip if installation fails.

- Before running local verification commands, install or sync the project's declared dependencies if they are not already available (for example: `make install`, `uv sync`, `npm install`/`yarn install`/`pnpm install`, `go mod download`) and the task requires those checks.
- If a focused verification command fails because a declared tool or dependency is missing (for example: `command not found`, `ModuleNotFoundError`, or a missing test runner/linter), first confirm it used the project's runner or existing virtual environment. If the dependency is still unavailable, try the appropriate project install/sync command once, then rerun the same focused verification. If installation still fails, report the blocker instead of silently skipping verification.
- Before ADDING a dependency the project doesn't already declare, confirm the task can't be solved with the standard library or a package already in the project's manifest/lockfile — prefer what's there.
- Vet any genuinely new package before adding it: actively maintained (recent release, responsive issues, more than a single maintainer, steady downloads), free of known unpatched CVEs (`npm audit` / `pip-audit` or the GitHub advisory DB), and under a permissive license (MIT, Apache-2.0, BSD). Do not add abandoned, single-source, or unlicensed packages. Pin or bound every newly added dependency to a specific version; never add a floating or unpinned dependency.
- For any dependency you add, surface it for human review through the response path in Source Context, then end your turn so the user can reply and the run can resume. This is an exception to the autonomy rule. List the package name, why it is needed, its maintenance/security status, and the alternatives you considered in the PR description too so a reviewer can veto it.
