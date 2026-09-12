# Intentional gap tests

These tests specify queue guarantees that Open SWE does not yet satisfy. They run in the normal pytest suite and must fail until the corresponding implementation gaps are closed.

```bash
uv run pytest -q tests/gaps
```

When a contract is implemented, move its test beside the production area it protects.
