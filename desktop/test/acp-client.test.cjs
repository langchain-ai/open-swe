const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const {
  AcpSession,
  dcodeTarget,
  promptBlocks,
  sessionTitle,
} = require("../src/acp-client.cjs")

test("uses the standard installed Python dcode command", (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-dcode-"))
  t.after(() => fs.rmSync(home, { recursive: true, force: true }))
  const command = path.join(home, ".local", "bin", "dcode")
  fs.mkdirSync(path.dirname(command), { recursive: true })
  fs.writeFileSync(command, "")
  const target = dcodeTarget({ env: { HOME: home }, platform: "darwin" })
  assert.deepEqual(target, {
    command,
    args: ["--acp"],
  })
})

test("supports an explicit dcode binary path", () => {
  assert.deepEqual(
    dcodeTarget({ env: { OPEN_SWE_DCODE_COMMAND: "/opt/bin/dcode" } }),
    { command: "/opt/bin/dcode", args: ["--acp"] }
  )
})

test("falls back to dcode on PATH", () => {
  assert.deepEqual(dcodeTarget({ env: {} }), {
    command: "dcode",
    args: ["--acp"],
  })
})

test("passes the selected model and effort to dcode", () => {
  assert.deepEqual(
    dcodeTarget({
      env: { OPEN_SWE_DCODE_COMMAND: "/opt/bin/dcode" },
      modelId: "anthropic:claude-sonnet-5",
      effort: "high",
    }),
    {
      command: "/opt/bin/dcode",
      args: [
        "--acp",
        "--model",
        "anthropic:claude-sonnet-5",
        "--model-params",
        '{"reasoning_effort":"high"}',
      ],
    }
  )
})

test("uses the first prompt as the local session title", () => {
  assert.equal(
    sessionTitle("  explain\nthis   project  "),
    "explain this project"
  )
  assert.equal(sessionTitle("   "), "New local agent")
})

test("builds ACP text and image prompt blocks", () => {
  assert.deepEqual(
    promptBlocks("  fix it  ", [
      {
        kind: "image",
        base64: "cG5n",
        mimeType: "image/png",
        fileName: "bug.png",
      },
    ]),
    [
      { type: "text", text: "fix it" },
      { type: "image", data: "cG5n", mimeType: "image/png" },
    ]
  )
})

test("switches model before continuing an ACP session", async () => {
  let closed = false
  let emitted
  const session = Object.assign(Object.create(AcpSession.prototype), {
    status: "idle",
    modelId: "old:model",
    effort: "low",
    target: { command: "old" },
    env: { MODEL: "old" },
    replayUsers: new Map(),
    rpc: { close: () => (closed = true) },
    connect(target, env) {
      this.target = target
      this.env = env
      this.rpc = { close() {} }
    },
    initialize: async function () {
      this.status = "idle"
    },
    notifyChange() {},
    emit: (event) => (emitted = event),
  })
  const workingTarget = { command: "new" }
  const workingEnv = { MODEL: "new" }

  await session.configure("new:model", "high", workingTarget, workingEnv)

  assert.equal(closed, true)
  assert.equal(session.modelId, "new:model")
  assert.equal(session.effort, "high")

  let attempts = 0
  session.initialize = async function () {
    if (attempts++ === 0) throw new Error("load failed")
    this.status = "idle"
  }
  await assert.rejects(session.configure("other:model", "low", {}, {}))
  assert.equal(session.status, "idle")
  assert.equal(session.target, workingTarget)
  assert.equal(session.env, workingEnv)
  assert.equal(session.modelId, "new:model")
  assert.equal(session.effort, "high")
  assert.deepEqual(emitted, { type: "error", message: "load failed" })
})

test("combines chunked user messages when replaying an ACP session", () => {
  const session = Object.assign(Object.create(AcpSession.prototype), {
    id: "session",
    title: "Restored session",
    events: [],
    replayUsers: new Map(),
    onEvent() {},
    onChange() {},
  })
  const replay = (text) =>
    session.handleNotification("session/update", {
      update: {
        sessionUpdate: "user_message_chunk",
        messageId: "message",
        content: { type: "text", text },
      },
    })

  replay("first ")
  replay("second")

  assert.equal(session.events[0].text, "first second")
})
