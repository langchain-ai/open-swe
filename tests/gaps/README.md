# Intentional gap tests

These tests specify queue guarantees that Open SWE does not yet satisfy. They are skipped during the normal pytest suite and must fail until the corresponding implementation gaps are closed.

```bash
OPEN_SWE_RUN_GAP_TESTS=1 uv run pytest -q tests/gaps
```

When a contract is implemented, move its test beside the production area it protects and remove the opt-in skip.
