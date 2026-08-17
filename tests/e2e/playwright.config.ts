import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const repoRoot = resolve(__dirname, "..", "..");
const PORT = Number(process.env.E2E_PORT ?? 2024);
const UI_PORT = Number(process.env.E2E_UI_PORT ?? 3100);
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests",
  testIgnore: "desktop.spec.ts",
  globalSetup: "./global-setup.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 60_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    // Always capture the replayable artifacts: a trace (DOM snapshots, network,
    // console, source — open with `npx playwright show-trace`) and a screen
    // recording, plus a screenshot on failure. The CI job uploads them.
    trace: "on",
    video: "on",
    screenshot: "only-on-failure",
    // SLOW_MO=700 npx playwright test --headed  → watch it run in human time.
    launchOptions: { slowMo: Number(process.env.SLOW_MO ?? 0) },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Real langgraph dev: real agent graph + real webhook routes + the harness
    // http app (fake GitHub/Slack + mock UIs). Only the LLM is faked.
    command:
      "uv run langgraph dev --config tests/e2e/langgraph.e2e.json " +
      `--port ${PORT} --no-browser --allow-blocking --no-reload`,
    cwd: repoRoot,
    url: `${baseURL}/mock/github/data`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    // Deterministic busy window for the interrupt-debounce spec: the fake LLM
    // holds the first run open this long so follow-ups reliably land mid-run.
    // E2E_UI_SERVER is where the harness proxies page requests for rendering;
    // global-setup starts that server on the same port.
    env: {
      ...process.env,
      E2E_BUSY_HOLD_SECONDS: "20",
      E2E_UI_SERVER: `http://127.0.0.1:${UI_PORT}`,
    },
  },
});
