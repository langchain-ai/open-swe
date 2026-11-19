import { defineConfig } from "@playwright/test";
import config from "./playwright.config";

const servers = Array.isArray(config.webServer) ? config.webServer : [];

export default defineConfig({
  ...config,
  testMatch: "account_recovery.spec.ts",
  testIgnore: [],
  outputDir: "test-results/auth",
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report/auth", open: "never" }],
    ["json", { outputFile: "playwright-report/auth/results.json" }],
  ],
  webServer: servers.slice(1).map((server) => ({
    ...server,
    reuseExistingServer: false,
    env: { ...server.env, E2E_OAUTH: "1" },
  })),
});
