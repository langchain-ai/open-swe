# Intentional browser gap tests

These Playwright tests cover contracts that need a real browser: long-transcript responsiveness and an interruption overtaking a queued follow-up before a cold reload. They are outside the default Playwright `testDir` and do not run in the green CI shards.

```bash
pnpm run test:e2e:gaps
```

The suite is expected to stay red until the product meets each contract. Calibrate timing budgets on CI before promoting a performance test into the default suite.
