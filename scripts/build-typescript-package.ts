import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"

const packageRoot = process.cwd()
const outputRoot = path.join(packageRoot, "dist")
const config = process.argv[2] || "tsconfig.build.json"
const compiler = path.join(
  packageRoot,
  "node_modules",
  "typescript",
  "bin",
  "tsc"
)

fs.rmSync(outputRoot, { recursive: true, force: true })
const result = spawnSync(process.execPath, [compiler, "-p", config], {
  cwd: packageRoot,
  stdio: "inherit",
})
if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status || 1)
