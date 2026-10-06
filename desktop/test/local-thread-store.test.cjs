const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { LocalThreadStore } = require("../src/local-thread-store.cjs");

const BRIDGE_ID = "0123456789abcdef0123456789abcdef";

function temporaryStore(t) {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-local-threads-"),
  );
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  let now = 100;
  return {
    path: path.join(root, "threads.json"),
    create: () =>
      new LocalThreadStore(path.join(root, "threads.json"), {
        now: () => ++now,
      }),
  };
}

test("keeps a thread's bridge and checkout across restarts, privately", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    id: "thread-1",
    cwd: path.resolve("/tmp/project"),
  });
  store.setBridge(thread.id, BRIDGE_ID);
  store.setCheckpoint(thread.id, {
    repo: path.resolve("/tmp/project"),
    ref: "refs/open-swe/local/thread-1",
    branch: "feature",
  });

  assert.equal(fs.statSync(fixture.path).mode & 0o777, 0o600);
  const restored = fixture.create().get(thread.id);
  assert.equal(restored.bridgeId, BRIDGE_ID);
  assert.equal(restored.checkpoint.branch, "feature");
  assert.throws(() => store.setBridge(thread.id, "../not-a-bridge"));
  assert.throws(() => store.create({ id: "thread-1", cwd: "/tmp/other" }));
});

test("remembers a created worktree after the thread moves off it", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    id: "thread-1",
    cwd: path.resolve("/tmp/project"),
  });
  const worktree = path.resolve("/tmp/worktrees/project-abcd1234");
  store.setWorktree(thread.id, worktree, true);
  store.setWorktree(thread.id, null);
  const reloaded = fixture.create().get(thread.id);
  assert.equal(reloaded.worktreePath, null);
  assert.deepEqual(reloaded.ownedWorktrees, [worktree]);
});
