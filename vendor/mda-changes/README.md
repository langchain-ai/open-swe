# Our changes to the vendored Managed Deep Agents SDK

One dated file per change we make in [`../managed-deepagents-sdk`](../managed-deepagents-sdk) ourselves (upstream pull requests we carry are recorded in [`../mda-upstream-prs`](../mda-upstream-prs) instead; see [`../README.md`](../README.md)), named `YYYY-MM-DD-<slug>.md` after the day it landed. Each opens with its verdict, then says:

- **What** we changed, and in which vendored files.
- **Why** Open SWE needs it.
- **Upstream:** what MDA needs before we can drop the change.

The verdict is one of two:

- **Upstream as-is.** The change is right for every MDA project, so we propose it unchanged and drop ours once `main` has it. It keeps the name we would propose.
- **Not upstream as-is.** It works for Open SWE but is the wrong shape for MDA: a workaround, a shortcut around a missing platform feature, or unsafe in general. Its options and functions are named `_openswe_*`, so nothing mistakes them for MDA's API, and the note describes the feature MDA should build instead and what Open SWE then switches to.

When upstream ships what a note asks for, move Open SWE onto it, let `uv run scripts/vendor.py` merge our edit away, and delete the note. `uv run scripts/vendor.py --diff` lists what is still ours.
