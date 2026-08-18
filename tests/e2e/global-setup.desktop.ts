import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import globalSetup from "./global-setup";

export default async function desktopGlobalSetup() {
  const repoRoot = resolve(__dirname, "..", "..");
  execFileSync("pnpm", ["--dir", "desktop", "run", "build"], {
    cwd: repoRoot,
    stdio: "inherit",
  });
  return globalSetup();
}
