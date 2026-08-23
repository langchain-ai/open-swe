import assert from "node:assert/strict"
import { EventEmitter } from "node:events"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import test from "node:test"

import {
  devApplicationTarget,
  packagedApplicationTarget,
} from "../build/application-supervisor.cjs"

test("both application targets run the combined entry on the Electron binary", () => {
  const repoRoot = path.resolve("/work/open-swe")
  const development = devApplicationTarget(repoRoot)
  assert.equal(development.command, process.execPath)
  assert.equal(development.args[0], path.join(repoRoot, "apps/graphs/dist/bin.js"))
  assert.equal(development.uiEntrypoint, path.join(repoRoot, "ui/.output/server/index.mjs"))

  const resources = path.resolve("/Applications/Open SWE.app/Contents/Resources")
  const packaged = packagedApplicationTarget(resources)
  assert.equal(packaged.command, process.execPath)
  assert.equal(packaged.args[0], path.join(resources, "local-backend/dist/bin.js"))
  assert.equal(packaged.uiEntrypoint, path.join(resources, "ui/server/index.mjs"))
})

test("restarts after the local server exits after becoming healthy", async (t) => {
  const harness = createSupervisorHarness()
  t.after(() => harness.close())

  const first = await harness.supervisor.start()
  harness.children[0].exitCode = 1
  harness.children[0].emit("exit", 1, null)
  await new Promise((resolve) => setImmediate(resolve))

  const second = await harness.supervisor.start()
  assert.equal(harness.children.length, 2)
  assert.notEqual(second.appUrl, first.appUrl)
})

test("restarts with a changed hosted backend URL", async (t) => {
  const harness = createSupervisorHarness("https://old.example/")
  t.after(() => harness.close())

  await harness.supervisor.start()
  assert.deepEqual(backendUrlArgument(harness.invocations[0]), "https://old.example/")
  assert.equal(harness.environments[0].ELECTRON_RUN_AS_NODE, "1")

  await harness.supervisor.setBackendUrl("https://new.example/")
  await harness.supervisor.start()
  assert.deepEqual(backendUrlArgument(harness.invocations[1]), "https://new.example/")
})

function backendUrlArgument(args: string[]): string | undefined {
  const index = args.indexOf("--backend-url")
  return index < 0 ? undefined : args[index + 1]
}

class FakeChild extends EventEmitter {
  exitCode: number | null = null
  signalCode: NodeJS.Signals | null = null

  kill(signal: NodeJS.Signals = "SIGTERM"): boolean {
    this.signalCode = signal
    queueMicrotask(() => this.emit("exit", null, signal))
    return true
  }
}

function createSupervisorHarness(backendUrl: string | null = null) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-application-supervisor-"))
  const entry = path.join(root, "apps", "graphs", "dist", "bin.js")
  const ui = path.join(root, "ui", ".output", "server", "index.mjs")
  fs.mkdirSync(path.dirname(entry), { recursive: true })
  fs.mkdirSync(path.dirname(ui), { recursive: true })
  fs.writeFileSync(entry, "")
  fs.writeFileSync(ui, "")

  const children: FakeChild[] = []
  const environments: NodeJS.ProcessEnv[] = []
  const invocations: string[][] = []
  let port = 41_000
  const { ApplicationSupervisor } = require("../build/application-supervisor.cjs")
  const supervisor = new ApplicationSupervisor({
    repoRoot: root,
    backendUrl,
    reservePort: async () => port++,
    fetch: async () => new Response(null, { status: 200 }),
    spawn: (_command, args, options) => {
      const child = new FakeChild()
      children.push(child)
      environments.push(options.env)
      invocations.push([...args])
      return child
    },
    stopTimeoutMs: 100,
  })

  return {
    supervisor,
    children,
    environments,
    invocations,
    close: async () => {
      await supervisor.close()
      fs.rmSync(root, { recursive: true, force: true })
    },
  }
}
