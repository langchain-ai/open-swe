const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const { LocalThreadStore } = require("../src/local-thread-store.cjs")

function temporaryStore(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-local-threads-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  let now = 100
  return {
    path: path.join(root, "threads.json"),
    create: () => new LocalThreadStore(path.join(root, "threads.json"), {
      now: () => ++now,
      uuid: () => "thread-1",
    }),
  }
}

test("persists a prompt until it is acknowledged", (t) => {
  const fixture = temporaryStore(t)
  const store = fixture.create()
  const thread = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "  fix the tests  ",
    images: [{ base64: "aW1n", mimeType: "image/png", fileName: "bug.png" }],
    modelId: "anthropic:test",
    effort: "high",
  })
  assert.equal(thread.title, "fix the tests")
  assert.equal(fs.statSync(fixture.path).mode & 0o777, 0o600)
  assert.deepEqual(store.pendingPrompt(thread.id), {
    prompt: "  fix the tests  ",
    images: [{ kind: "image", base64: "aW1n", mimeType: "image/png", fileName: "bug.png" }],
  })
  assert.deepEqual(store.pendingPrompt(thread.id), store.pendingPrompt(thread.id))
  store.clearPrompt(thread.id)
  assert.equal(store.pendingPrompt(thread.id), null)
  const restored = fixture.create().get(thread.id)
  assert.equal(restored.pending, null)
  assert.equal(restored.modelId, "anthropic:test")
})

test("reconciles interrupted threads and retains checkpoint refs until deletion", (t) => {
  const fixture = temporaryStore(t)
  const store = fixture.create()
  const thread = store.create({ cwd: path.resolve("/tmp/project"), prompt: "work" })
  store.setCheckpoint(thread.id, { repo: path.resolve("/tmp/project"), ref: "refs/open-swe/local/thread-1" })
  store.update(thread.id, { status: "running" })

  const restored = fixture.create()
  assert.equal(restored.get(thread.id).status, "error")
  assert.equal(restored.get(thread.id).checkpoint.ref, "refs/open-swe/local/thread-1")
  assert.equal(restored.delete(thread.id).checkpoint.ref, "refs/open-swe/local/thread-1")
  assert.equal(restored.get(thread.id), null)
})
