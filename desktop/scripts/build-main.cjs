const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
// The bridge client is a workspace package the main process requires at runtime.
execFileSync("pnpm", ["--filter", "open-swe-bridge-client", "run", "build"], {
  cwd: root,
  stdio: "inherit",
});
execFileSync(
  path.join(root, "node_modules", ".bin", "tsc"),
  ["-p", "tsconfig.json"],
  {
    cwd: root,
    stdio: "inherit",
  },
);
for (const file of ["local-thread-store.cjs"]) {
  fs.copyFileSync(path.join(root, "src", file), path.join(root, "build", file));
}
