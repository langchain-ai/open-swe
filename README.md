# Open SWE Operator Docs

This repository currently centers on `local_fix_agent.py`: a guarded repair CLI for local or SSH-backed repositories. The operator docs below describe the tool as it behaves today.

## Start Here

- Local repair: `fixit pytest tests/test_x.py -q`
- Remote repair: `fixit --target edge-01 --repo /srv/app "pytest -q"`
- Reuse recent state: `python local_fix_agent.py --last`
- Publish validated run: `AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr`
- Publish current repo state: `AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr`
- Safe self-fork merge: add `--publish-merge`
- Sync local main after merge: add `--publish-merge-local-main`

## Operator Docs

- [Operator Guide](./docs/README.md)
- [Runbook](./docs/RUNBOOK.md)
- [Remote Mode](./docs/REMOTE_MODE.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)
- [Wrapper: scripts/fixpublish.sh](./scripts/fixpublish.sh)
- [Wrapper: scripts/publishcurrent.sh](./scripts/publishcurrent.sh)

## Current Workflow Summary

- The repair loop reasons locally, edits through guarded tools, reruns validation, and only commits after extra checks pass.
- Remote mode keeps reasoning local and executes shell/file/git operations over one SSH session.
- Publish validated-run mode and publish-current mode are separate workflows.
- Publish summaries report target resolution, control path, PR status, merge status, and next actions.
- Blocked states are reported directly with explicit evidence and operator actions.
