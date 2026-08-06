const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const {
  appendThreadEvent,
  readThread,
  readThreads,
  removeThread,
  writeThread,
} = require("../src/local-thread-store.cjs")

test("persists local thread history and resume metadata across restarts", (t) => {
  const storePath = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-threads-"))
  t.after(() => fs.rmSync(storePath, { recursive: true, force: true }))
  const session = {
    id: "thread-1",
    cwd: "/tmp/example",
    title: "Fix the bug",
    status: "running",
    createdAt: 1,
    updatedAt: 2,
    providerSessionId: "provider-1",
    canResume: true,
  }

  writeThread(storePath, session)
  appendThreadEvent(storePath, session, {
    sequence: 0,
    timestamp: "2026-08-05T20:00:00Z",
    type: "user-message",
    text: "Fix it",
  })
  appendThreadEvent(
    storePath,
    { ...session, status: "idle", updatedAt: 3 },
    {
      sequence: 1,
      timestamp: "2026-08-05T20:00:01Z",
      type: "run-end",
    }
  )

  assert.deepEqual(readThread(storePath, session.id), {
    ...session,
    status: "stopped",
    updatedAt: 3,
    events: [
      {
        sequence: 0,
        timestamp: "2026-08-05T20:00:00Z",
        type: "user-message",
        text: "Fix it",
      },
      {
        sequence: 1,
        timestamp: "2026-08-05T20:00:01Z",
        type: "run-end",
      },
    ],
  })
  assert.deepEqual(readThreads(storePath), [
    { ...session, status: "stopped", updatedAt: 3 },
  ])
  removeThread(storePath, session.id)
  assert.equal(readThread(storePath, session.id), null)
})
