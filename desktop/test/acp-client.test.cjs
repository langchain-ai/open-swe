const test = require("node:test")
const assert = require("node:assert/strict")

const {
  AcpSession,
  dcodeTarget,
  promptBlocks,
  sessionTitle,
} = require("../build/acp-client.cjs")

test("runs ACP from the pinned uv package", () => {
  const launcherArgs = [
    "tool",
    "run",
    "--isolated",
    "--python",
    "3.13",
    "--from",
    "deepagents-code[fireworks]==0.1.56",
    "dcode",
  ]
  assert.deepEqual(dcodeTarget({ env: {} }), {
    command: "uv",
    args: [...launcherArgs, "--acp"],
    launcherArgs,
    env: {
      DEEPAGENTS_CODE_AUTO_UPDATE: "0",
      PYTHONDONTWRITEBYTECODE: "1",
    },
  })
})

test("supports an explicit dcode model override", () => {
  const target = dcodeTarget({
    env: { OPEN_SWE_DCODE_MODEL: "e2e:fake" },
    modelId: "anthropic:claude-sonnet-5",
  })
  assert.deepEqual(target.args.slice(-3), ["--acp", "--model", "e2e:fake"])
})

test("passes the selected model and effort to dcode", () => {
  const target = dcodeTarget({
    env: {},
    modelId: "anthropic:claude-sonnet-5",
    effort: "high",
  })
  assert.deepEqual(target.args.slice(-5), [
    "--acp",
    "--model",
    "anthropic:claude-sonnet-5",
    "--model-params",
    '{"reasoning_effort":"high"}',
  ])
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
