import { defineConfig } from "@playwright/test";

import baseConfig from "./playwright.config";

export default defineConfig({
  ...baseConfig,
  webServer: undefined,
  globalSetup: "./global-setup.desktop.ts",
  testIgnore: [],
  testMatch: "desktop.spec.ts",
  outputDir: "test-results/desktop",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report/desktop", open: "never" }],
  ],
  use: {
    ...baseConfig.use,
    trace: "retain-on-failure",
    video: "off",
    screenshot: "off",
  },
  projects: [{ name: "electron" }],
});
