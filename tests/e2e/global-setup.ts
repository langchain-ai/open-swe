import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

// Build the real ui/ SPA once so the harness can serve it same-origin. The API
// base is baked in at build time, so it must match the harness port. Set
// E2E_FORCE_UI_BUILD=1 to rebuild (e.g. after changing the port or the UI).
export default function globalSetup() {
  const repoRoot = resolve(__dirname, "..", "..");
  const ui = resolve(repoRoot, "ui");
  const shell = resolve(ui, ".output", "public", "_shell.html");
  const port = process.env.E2E_PORT ?? "2024";

  if (existsSync(shell) && !process.env.E2E_FORCE_UI_BUILD) return;

  if (!existsSync(resolve(ui, "node_modules"))) {
    execSync("pnpm install --frozen-lockfile", { cwd: ui, stdio: "inherit" });
  }
  execSync("pnpm run build", {
    cwd: ui,
    stdio: "inherit",
    env: {
      ...process.env,
      VITE_DASHBOARD_API_BASE_URL: `http://127.0.0.1:${port}`,
    },
  });
}
