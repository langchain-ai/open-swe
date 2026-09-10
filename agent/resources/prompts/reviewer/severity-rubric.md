Severity reflects runtime consequence:

- `critical` — panic, crash, data loss, auth bypass, or security regression.
- `high` — wrong result for users or another clear correctness bug.
- `medium` — edge-case correctness bug or concurrency hazard with a reachable trigger.
- `low` — concrete defect with limited blast radius, such as a broken binding, wrong hot-path log level, or user-visible UX bug.

Architectural opinions, naming preferences, and micro-performance concerns are not findings.
