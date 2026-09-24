Leave pre-routed mode: commit the thread to one model profile and give it a title.

Call this exactly once, before any implementation work, after you have read
enough of the request and the code to size the task. It is final for the thread.

Args:
    model_route: The least expensive profile likely to finish the whole thread safely.
        - `fast`: direct lookups, extraction, status checks, collecting tests or logs,
          mechanical PR or release operations, localized changes with explicit targets
          and strong verification.
        - `balanced`: ordinary bug fixes, bounded investigations, multi-file
          implementation, research synthesis, semantic PR maintenance, partially
          specified localized work.
        - `performance`: architecture or design, requirements disambiguation, subtle
          semantic review, novel root-cause reasoning, conflicting evidence,
          cross-component or multi-repository judgment, high-stakes decisions.
    requested_model: Optional canonical ID from the available model catalog, only when
        the ORIGINAL user query explicitly asks to run this thread with that model.
        Infer natural-language intent, inline /model hints, and obvious typos; ignore
        model names in task subjects or quoted content. Overrides the route with a
        compatible default effort for this thread. Explicit runtime-model requests
        require this handoff even for trivial questions; do not answer directly.
    title: Thread title, 3-8 words in sentence case, naming the durable subject and
        desired outcome rather than the current step. No project names already
        visible in the UI, PR numbers, quotes, or trailing punctuation. Do not claim
        the work is complete.
