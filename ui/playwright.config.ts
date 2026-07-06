import { defineConfig, devices } from "@playwright/test"

const PORT = 3000
const BASE_URL = `http://localhost:${PORT}`

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    // The Copy sandbox ID action writes to the clipboard; grant read so the
    // test can assert what landed there.
    permissions: ["clipboard-read", "clipboard-write"],
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // Emulate an iPad: Chromium in mobile mode reports `(hover: none)` /
      // `(pointer: coarse)`, which is what gates the touch-only kebab menu.
      name: "ipad-touch",
      use: {
        browserName: "chromium",
        viewport: { width: 834, height: 1112 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
