Approve the current plan, route the thread to a model profile, and exit plan mode.

Call this when the user approves the plan, asks to leave plan mode, or asks to
start implementing the approved plan. Implementation runs on the profile you
choose here, so size it from the approved plan, not from the planning work.

Args:
    model_route: The least expensive profile likely to finish the implementation safely.
        - `fast`: localized changes with explicit targets and strong verification.
        - `balanced`: ordinary bug fixes, bounded multi-file implementation.
        - `performance`: architecture-level changes, subtle semantics, cross-component
          or multi-repository judgment, high-stakes decisions.
    title: Thread title, 3-8 words in sentence case, naming the durable subject and
        desired outcome. No project names already visible in the UI, PR numbers,
        quotes, or trailing punctuation. Do not claim the work is complete.
