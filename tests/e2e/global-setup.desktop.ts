import { execFileSync } from "node:child_process";
import path from "node:path";

export default function desktopGlobalSetup(): void {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const nodePath = path.join(
    repoRoot,
    "desktop",
    "node_modules",
    "node",
    "bin",
  );
  const env = {
    ...process.env,
    PATH: `${nodePath}${path.delimiter}${process.env.PATH ?? ""}`,
  };
  const packageManager = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
  for (const [directory, script] of [
    [repoRoot, "build:graphs"],
    [path.join(repoRoot, "ui"), "build"],
    [path.join(repoRoot, "desktop"), "build"],
  ] as const) {
    execFileSync(packageManager, ["run", script], {
      cwd: directory,
      env,
      stdio: "inherit",
    });
  }
}
