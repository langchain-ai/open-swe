const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const {
  readAcpSessions,
  writeAcpSessions,
} = require("../build/acp-session-store.cjs")

test("persists valid ACP session metadata and ignores malformed records", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-acp-sessions-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const filePath = path.join(root, "sessions.json")
  const session = {
    id: "desktop-id",
    acpSessionId: "acp-id",
    cwd: root,
    title: "Persist this",
    createdAt: 123,
    updatedAt: 456,
    modelId: "provider:model",
    effort: "high",
    dcodeCommand: "/opt/bin/dcode",
    checkpoint: { repo: root, ref: "refs/open-swe/local/desktop-id" },
  }

  writeAcpSessions(filePath, [session])
  assert.deepEqual(readAcpSessions(filePath), [session])
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(filePath).mode & 0o777, 0o600)
  }

  fs.writeFileSync(
    filePath,
    JSON.stringify([session, { ...session, id: "bad", cwd: "relative" }])
  )
  assert.deepEqual(readAcpSessions(filePath), [session])
})
