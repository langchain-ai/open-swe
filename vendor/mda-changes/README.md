# Our changes to the vendored Managed Deep Agents SDK

One dated file per change we make in [`../managed-deepagents-sdk`](../managed-deepagents-sdk), named `YYYY-MM-DD-<slug>.md` after the day it landed. Each says:

- **What** we changed, and in which vendored files.
- **Why** Open SWE needs it.
- **Upstream:** what MDA itself needs before we can drop the change, as a feature any MDA project could use, and the upstream PR or issue for it.

Options and functions that exist only for Open SWE are named `_openswe_*`, so nothing else mistakes them for MDA's API. When upstream ships what a file asks for, move Open SWE onto it, let `uv run scripts/vendor.py` merge our edit away, and delete the file. `uv run scripts/vendor.py --diff` lists what is still ours.
