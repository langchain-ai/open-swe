const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
execFileSync(
  path.join(root, "node_modules", ".bin", "tsc"),
  ["-p", "tsconfig.json"],
  {
    cwd: root,
    stdio: "inherit",
  },
);
for (const file of [
  "backend-supervisor.cjs",
  "device-identity.cjs",
  "local-execution-context.cjs",
  "local-run-reporter.cjs",
]) {
  fs.copyFileSync(path.join(root, "src", file), path.join(root, "build", file));
}
