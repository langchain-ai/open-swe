# Default Prompt

When a repository is not explicitly mentioned, use the repository provided in the run metadata or dashboard settings. Do not assume a hardcoded repository name.

## Push Access

Before doing more than a brief read-only investigation, confirm that open-swe[bot] has push access to the target repo. The Slack-mention setup path injects an explicit `## Push Access Pre-check` or `## PUSH IS NOT AUTHORIZED` block into your prompt; if that block is absent (dashboard- or GitHub-triggered runs that bypass that setup), verify push access yourself with one cheap probe (e.g. `GH_TOKEN=dummy gh api repos/<owner>/<repo> --jq .permissions.push`) before investing investigation tokens. If push is not authorized, tell the user up front, ask whether to proceed read-only and post a diff for manual application or whether they can grant the Open SWE GitHub App write access first, and do not silently retry `git push` on follow-up mentions.
