import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

// Dev-only checks for the Trace Flight lab pages, served by the lab's own Vite
// config and driven through window.traceFlight / window.traceFlightLab. The
// production suite (playwright.config.ts) never loads these specs.
const PORT = Number(process.env.LAB_PORT ?? 3150);
const uiDir = resolve(__dirname, "..", "..", "ui");

export default defineConfig({
  testDir: "./lab",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `pnpm --dir ${uiDir} lab --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
