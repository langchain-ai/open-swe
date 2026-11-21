# Our changes to the vendored langchain-quickjs

One dated file per change we make in [`../langchain-quickjs`](../langchain-quickjs) ourselves (upstream pull requests we carry are recorded in `../langchain-quickjs-upstream-prs` instead; see [`../README.md`](../README.md)), named `YYYY-MM-DD-<slug>.md` after the day it landed. Each opens with its verdict, then says:

- **What** we changed, and in which vendored files.
- **Why** Robin needs it.
- **Upstream:** what upstream needs before we can drop the change.

The verdict is one of two:

- **Upstream as-is.** The change is right for every user of the package, so we propose it unchanged and drop ours once `main` has it. It keeps the name we would propose.
- **Not upstream as-is.** It works for Robin but is the wrong shape upstream: a workaround, a shortcut around a missing feature, or unsafe in general. Its options and functions are named `_openswe_*`, so nothing mistakes them for upstream's API, and the note describes the feature upstream should build instead and what Robin then switches to.

When upstream ships what a note asks for, move Robin onto it, let `uv run scripts/vendor.py` merge our edit away, and delete the note. `uv run scripts/vendor.py --diff` lists what is still ours.
