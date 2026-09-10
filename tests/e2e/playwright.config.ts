import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const repoRoot = resolve(__dirname, "..", "..");
const PORT = Number(process.env.E2E_PORT ?? 2024);
const UI_PORT = Number(process.env.E2E_UI_PORT ?? 3100);
const harnessURL = `http://127.0.0.1:${PORT}`;
// The app's own server, as a deployed browser sees it: it serves the pages and
// fronts `/dashboard/api/*` itself. Driving the harness origin instead skipped
// the app server's proxy, which is how a broken proxy shipped past a green run.
const baseURL = `http://127.0.0.1:${UI_PORT}`;

export default defineConfig({
  testDir: "./tests",
  testIgnore: "desktop.spec.ts",
  globalSetup: "./global-setup.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  // Nearly every assertion here settles in under two seconds; the handful that
  // wait on a real agent run pass their own longer timeout. A low default is
  // what keeps a genuine failure from burning 60s per assertion before the run
  // goes red.
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    // Recording a trace (DOM snapshots, network, console, source — open with
    // `pnpm exec playwright show-trace`) and a video costs real time on every
    // spec, pass or fail. Default to keeping them only for failures; set
    // E2E_ARTIFACTS=1 to capture everything, which is what you want when
    // debugging a spec that passes but does the wrong thing.
    trace: process.env.E2E_ARTIFACTS
      ? "on"
      : process.env.CI
        ? "on-first-retry"
        : "retain-on-failure",
    video: process.env.E2E_ARTIFACTS
      ? "on"
      : process.env.CI
        ? "on-first-retry"
        : "retain-on-failure",
    screenshot: "only-on-failure",
    // SLOW_MO=700 pnpm exec playwright test --headed  → watch it run in human time.
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
    url: `${harnessURL}/mock/github/data`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    // Busy window for the specs that hold a run open so follow-ups land
    // mid-run. `E2E_BUSY_HOLD:<n>` overrides it per message; this is the
    // fallback for the specs that just say `E2E_BUSY_HOLD` and then cancel the
    // run, so it only has to outlast the assertions they make while it is busy.
    // E2E_UI_SERVER is the app's own server, which global-setup starts on that
    // port; the harness needs it to address the dashboard in "Open in Web" links.
    env: {
      ...process.env,
      E2E_BUSY_HOLD_SECONDS: "8",
      E2E_UI_SERVER: `http://127.0.0.1:${UI_PORT}`,
      E2E_EXIT_WHEN_ORPHANED: "1",
    },
  },
});
