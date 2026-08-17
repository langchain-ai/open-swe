import { defineConfig } from "@playwright/test";

import baseConfig from "./playwright.config";

export default defineConfig({
  ...baseConfig,
  testMatch: "desktop.spec.ts",
  outputDir: "test-results/desktop",
  timeout: 180_000,
  expect: { timeout: 120_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report/desktop", open: "never" }],
  ],
  use: {
    ...baseConfig.use,
    trace: "off",
    video: "off",
    screenshot: "off",
  },
  projects: [{ name: "electron" }],
});
