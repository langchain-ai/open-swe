# Intentional browser gap tests

These Playwright tests cover contracts that need a real browser: long-transcript responsiveness and an interruption overtaking a queued follow-up before a cold reload. They run in the normal browser CI shards and must fail until the corresponding implementation gaps are closed.

```bash
pnpm run test:e2e:gaps
```

The suite is expected to stay red until the product meets each contract. Calibrate timing budgets on CI before promoting a performance test into the default suite.
