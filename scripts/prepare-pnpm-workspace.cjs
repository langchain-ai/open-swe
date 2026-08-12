const fs = require("node:fs")
const path = require("node:path")
const { spawnSync } = require("node:child_process")

const root = path.resolve(__dirname, "..")
const legacyInstalls = ["ui", "desktop"]
  .map((workspace) => path.join(root, workspace, "node_modules"))
  .filter((nodeModules) => fs.existsSync(path.join(nodeModules, ".modules.yaml")))

if (legacyInstalls.length > 0) {
  for (const nodeModules of legacyInstalls) {
    console.log(`Removing legacy ${path.relative(root, nodeModules)}`)
    fs.rmSync(nodeModules, { recursive: true, force: true })
  }

  const result = spawnSync("pnpm", ["install", "--frozen-lockfile"], {
    cwd: root,
    stdio: "inherit",
    shell: process.platform === "win32",
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
