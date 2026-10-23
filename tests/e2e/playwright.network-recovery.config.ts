import { defineConfig } from "@playwright/test"

/**
 * Desktop network-recovery acceptance. Independent of the full SaaS harness:
 * it drives the real Electron app in local-only mode against a deterministic
 * toy LangGraph backend, so the three outage modes can be measured in isolation.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: "network_recovery.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 60_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report/network-recovery", open: "never" }]],
  use: {
    trace: "retain-on-failure",
    video: "off",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "electron" }],
})
