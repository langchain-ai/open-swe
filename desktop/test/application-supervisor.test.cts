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

test("development application target runs the built TanStack server with Node 24", () => {
  const repoRoot = path.resolve("/work/open-swe")
  expectTarget(devApplicationTarget(repoRoot), {
    command: path.join(repoRoot, "desktop/node_modules/node/bin/node"),
    script: path.join(repoRoot, "ui/.output/server/index.mjs"),
    cwd: path.join(repoRoot, "ui/.output"),
  })
})

test("packaged application target shares the bundled Node 24 runtime", () => {
  const resources = path.resolve("/Applications/Open SWE.app/Contents/Resources")
  expectTarget(packagedApplicationTarget(resources), {
    command: path.join(resources, "local-backend/runtime/bin/node"),
    script: path.join(resources, "ui/server/index.mjs"),
    cwd: path.join(resources, "ui"),
  })
})

test("restarts after the TanStack server exits after becoming healthy", async (t) => {
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
  assert.equal(harness.environments[0].DASHBOARD_API_URL, "https://old.example/")

  await harness.supervisor.setBackendUrl("https://new.example/")
  await harness.supervisor.start()
  assert.equal(harness.environments[1].DASHBOARD_API_URL, "https://new.example/")
})

function expectTarget(
  actual: { command: string; args: string[]; cwd: string },
  expected: { command: string; script: string; cwd: string }
): void {
  assert.equal(actual.command, expected.command)
  assert.deepEqual(actual.args, [expected.script])
  assert.equal(actual.cwd, expected.cwd)
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
  const command = path.join(root, "desktop", "node_modules", "node", "bin", "node")
  const script = path.join(root, "ui", ".output", "server", "index.mjs")
  fs.mkdirSync(path.dirname(command), { recursive: true })
  fs.mkdirSync(path.dirname(script), { recursive: true })
  fs.writeFileSync(command, "")
  fs.writeFileSync(script, "")

  const children: FakeChild[] = []
  const environments: NodeJS.ProcessEnv[] = []
  let port = 41_000
  const { ApplicationSupervisor } = require("../build/application-supervisor.cjs")
  const supervisor = new ApplicationSupervisor({
    graph: {
      start: async () => ({ origin: "http://127.0.0.1:42000", token: "test-token" }),
    },
    repoRoot: root,
    backendUrl,
    reservePort: async () => port++,
    fetch: async () => new Response(null, { status: 200 }),
    spawn: (_command, _args, options) => {
      const child = new FakeChild()
      children.push(child)
      environments.push(options.env)
      return child
    },
    stopTimeoutMs: 100,
  })

  return {
    supervisor,
    children,
    environments,
    close: async () => {
      await supervisor.close()
      fs.rmSync(root, { recursive: true, force: true })
    },
  }
}
