import { spawnSync } from "node:child_process"
import { createRequire } from "node:module"

const require = createRequire(import.meta.url)
const builder = require.resolve("electron-builder/cli.js")
const directoryOnly = process.argv.slice(2).includes("--dir")
const result = spawnSync(process.execPath, [
  builder,
  ...(directoryOnly ? ["--dir"] : []),
  "--publish",
  "never",
], {
  stdio: "inherit",
  env: {
    ...process.env,
    CSC_IDENTITY_AUTO_DISCOVERY: "false",
  },
})

if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status ?? 1)
