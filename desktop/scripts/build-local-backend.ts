import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(scriptDirectory, "..")
const repositoryRoot = path.resolve(desktopRoot, "..")
const outputRoot = path.join(desktopRoot, "resources", "local-backend")
// The packaged server runs on the Electron binary in Node mode, so nothing but
// a Node for this build is needed — and none is copied into the app.
const buildRuntime = path.join(
  desktopRoot,
  "node_modules",
  "node",
  "bin",
  process.platform === "win32" ? "node.exe" : "node"
)
const packageManager = process.platform === "win32" ? "pnpm.cmd" : "pnpm"

function forbiddenRuntimeFiles(root: string): string[] {
  const found: string[] = []
  const visit = (directory: string): void => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name)
      if (entry.isDirectory()) {
        visit(absolute)
        continue
      }
      const relative = path.relative(root, absolute)
      if (
        /\.(?:py|pyc|pyo)$/i.test(entry.name) ||
        /^python(?:\d+(?:\.\d+)*)?(?:\.exe)?$/i.test(entry.name) ||
        /^uv(?:\.exe)?$/i.test(entry.name) ||
        entry.name === "pyvenv.cfg"
      ) {
        found.push(relative)
      }
    }
  }
  visit(root)
  return found
}

function runtimeOnPath(): string {
  return fs.existsSync(buildRuntime) ? buildRuntime : process.execPath
}

function run(args: string[]): void {
  const result = spawnSync(packageManager, args, {
    cwd: repositoryRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      PATH: `${path.dirname(runtimeOnPath())}${path.delimiter}${process.env.PATH || ""}`,
    },
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status || 1)
}

run(["run", "build:graphs"])
fs.rmSync(outputRoot, { recursive: true, force: true })
run([
  "--filter",
  "@open-swe/graphs",
  "deploy",
  "--prod",
  outputRoot,
])
const forbidden = forbiddenRuntimeFiles(outputRoot)
if (forbidden.length > 0) {
  throw new Error(
    `Local backend package contains forbidden Python or uv files:\n${forbidden.join("\n")}`
  )
}
