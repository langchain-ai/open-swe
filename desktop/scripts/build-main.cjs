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
  "device-identity.cjs",
  "local-checkpoint-store.cjs",
  "local-runner.cjs",
]) {
  fs.copyFileSync(path.join(root, "src", file), path.join(root, "build", file));
}
