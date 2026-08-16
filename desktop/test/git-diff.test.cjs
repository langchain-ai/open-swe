const test = require("node:test")
const assert = require("node:assert/strict")
const { execFileSync } = require("node:child_process")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const {
  captureCheckpoint,
  checkpointRef,
  currentBranch,
  readDiff,
  repoRoot,
} = require("../src/git-diff.cjs")

function git(cwd, args) {
  execFileSync("git", args, { cwd, stdio: "ignore" })
}

test("diffs the worktree against a session checkpoint", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-git-"))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  git(dir, ["init", "-q", "-b", "main"])
  git(dir, ["config", "user.email", "test@example.com"])
  git(dir, ["config", "user.name", "Test"])
  fs.writeFileSync(path.join(dir, "kept.txt"), "one\ntwo\n")
  fs.writeFileSync(path.join(dir, "gone.txt"), "bye\n")
  git(dir, ["add", "-A"])
  git(dir, ["commit", "-qm", "init"])

  const repo = await repoRoot(dir)
  assert.equal(await currentBranch(dir), "main")
  const ref = checkpointRef("session-id")
  await captureCheckpoint(repo, ref)

  fs.writeFileSync(path.join(dir, "kept.txt"), "one\ntwo\nthree\n")
  fs.writeFileSync(path.join(dir, "added.txt"), "fresh\n")
  fs.writeFileSync(path.join(dir, "binary.dat"), Buffer.from([0, 1, 2, 0]))
  fs.writeFileSync(path.join(dir, "huge.txt"), "x".repeat(500_000))
  fs.rmSync(path.join(dir, "gone.txt"))

  const diff = await readDiff(repo, ref)
  assert.equal(diff.status, "ready")
  assert.equal(diff.truncated, false)
  assert.deepEqual(
    diff.files.map((file) => [file.path, file.status, file.additions, file.deletions]),
    [
      ["added.txt", "added", 1, 0],
      ["binary.dat", "added", 0, 0],
      ["gone.txt", "removed", 0, 1],
      ["huge.txt", "added", 1, 0],
      ["kept.txt", "modified", 1, 0],
    ]
  )
  const kept = diff.files.find((file) => file.path === "kept.txt")
  assert.equal(kept.originalContent, "one\ntwo\n")
  assert.equal(kept.modifiedContent, "one\ntwo\nthree\n")
  assert.equal(kept.unrenderable, false)
  assert.equal(diff.files.find((file) => file.path === "binary.dat").unrenderable, true)
  assert.equal(diff.files.find((file) => file.path === "gone.txt").modifiedContent, null)

  // Oversized blobs are never read into memory, only reported.
  const huge = diff.files.find((file) => file.path === "huge.txt")
  assert.equal(huge.unrenderable, true)
  assert.equal(huge.modifiedContent, null)
})
