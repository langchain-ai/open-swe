# Our changes to the vendored langchain-quickjs

One dated file per change we make in [`../langchain-quickjs`](../langchain-quickjs) ourselves (upstream pull requests we carry are recorded in `../langchain-quickjs-upstream-prs` instead; see [`../README.md`](../README.md)), named `YYYY-MM-DD-<slug>.md` after the day it landed. Each opens with its verdict and then says what changed, why Open SWE needs it and what upstream needs before we can drop it, as [`../mda-changes/README.md`](../mda-changes/README.md) describes.

`uv run scripts/vendor.py --project langchain-quickjs --diff` lists what is still ours.
