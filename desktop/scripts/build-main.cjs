const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const appVariant = process.env.OPEN_SWE_DESKTOP_APP_VARIANT || "open-swe";
if (!["open-swe", "agent-workspace"].includes(appVariant)) {
  throw new Error(`Unknown desktop app variant: ${appVariant}`);
}
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
fs.writeFileSync(
  path.join(root, "build", "app-variant.cjs"),
  `module.exports = ${JSON.stringify(appVariant)};\n`,
);
fs.copyFileSync(
  path.join(root, "src", "local-thread-store.cjs"),
  path.join(root, "build", "local-thread-store.cjs"),
);
