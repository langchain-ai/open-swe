const test = require("node:test")
const assert = require("node:assert/strict")
const { createHash } = require("node:crypto")

const {
  AcpSession,
  dcodeTarget,
  promptBlocks,
  sessionTitle,
} = require("../build/acp-client.cjs")

test("runs ACP through the isolated uv environment", () => {
  const target = dcodeTarget({ env: {} })
  assert.equal(target.command, "uv")
  assert.deepEqual(target.args, [...target.launcherArgs, "--acp"])
  assert.ok(target.launcherArgs.includes("--isolated"))
  assert.ok(target.launcherArgs.includes("3.14"))
  assert.ok(target.launcherArgs.includes("deepagents==0.7.6"))
  assert.deepEqual(target.env, {
    DEEPAGENTS_CODE_AUTO_UPDATE: "0",
    PYTHONDONTWRITEBYTECODE: "1",
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

test("builds escaped desktop ACP messages with native image blocks", () => {
  assert.deepEqual(
    promptBlocks('  fix <tag attr="value"> & it  ', [
      {
        kind: "image",
        base64: "cG5n",
        mimeType: "image/png",
        fileName: "bug.png",
      },
    ]),
    [
      {
        type: "text",
        text: `<input-message sender="desktop:local" surface="desktop" kind="human">
  <content>fix &lt;tag attr=&quot;value&quot;&gt; &amp; it</content>
</input-message>`,
      },
      { type: "image", data: "cG5n", mimeType: "image/png" },
    ]
  )
})

test("optionally introduces the local desktop identity before a prompt", () => {
  const blocks = promptBlocks("fix it", [], true)

  assert.equal(blocks.length, 2)
  assert.match(
    blocks[0].text,
    /^<dynamic-context kind="person" id="desktop:local" hash="[a-f0-9]{64}">/,
  )
  const context = blocks[0].text
  const hash = context.match(/ hash="([a-f0-9]{64})"/)?.[1]
  assert.equal(
    hash,
    createHash("sha256").update(context.replace(` hash="${hash}"`, "")).digest("hex"),
  )
  assert.equal(
    blocks[1].text,
    `<input-message sender="desktop:local" surface="desktop" kind="human">
  <content>fix it</content>
</input-message>`,
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

test("introduces the desktop identity once even when the prompt fails", async () => {
  const prompts = []
  const changes = []
  const session = Object.assign(Object.create(AcpSession.prototype), {
    status: "idle",
    closed: false,
    identityIntroduced: false,
    acpSessionId: "session",
    events: [],
    replayUsers: new Map(),
    onEvent() {},
    onChange: (value) => changes.push(value.identityIntroduced),
    rpc: {
      request: async (_method, params) => {
        prompts.push(params.prompt)
        if (prompts.length === 1) throw new Error("failed")
      },
    },
  })

  await assert.rejects(session.prompt("first", []), /failed/)
  await session.prompt("second", [])

  assert.equal(prompts[0][0].text.startsWith("<dynamic-context"), true)
  assert.equal(prompts[1][0].text.startsWith("<input-message"), true)
  assert.equal(changes.includes(true), true)
  assert.equal(changes.indexOf(true), changes.lastIndexOf(false) + 1)
})

test("restored structured replays prevent duplicate identity introductions", () => {
  const session = Object.assign(Object.create(AcpSession.prototype), {
    id: "session",
    title: "Restored session",
    events: [],
    identityIntroduced: false,
    replayUsers: new Map(),
    onEvent() {},
    onChange() {},
  })
  const introduction = promptBlocks("ignored", [], true)[0].text

  session.handleNotification("session/update", {
    update: {
      sessionUpdate: "user_message_chunk",
      messageId: "identity",
      content: { type: "text", text: introduction },
    },
  })

  assert.equal(session.identityIntroduced, true)
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
