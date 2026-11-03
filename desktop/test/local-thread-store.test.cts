const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { runInNewContext } = require("node:vm");
const { deleteRefs, removeWorktree } = require("../build/git-diff.cjs");

const { LocalThreadStore } = require("../build/local-thread-store.cjs");

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

test("task folders are distinct, persistent and retained when history is removed", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const first = store.createTask("10000000-0000-4000-8000-000000000001");
  const second = store.createTask("10000000-0000-4000-8000-000000000002");
  assert.notEqual(first.cwd, second.cwd);
  fs.writeFileSync(path.join(first.cwd, "report.txt"), "keep me");
  const reloaded = fixture.create();
  assert.equal(reloaded.taskRoot(reloaded.get(first.id)), first.cwd);
  reloaded.delete(first.id);
  assert.equal(
    fs.readFileSync(path.join(first.cwd, "report.txt"), "utf8"),
    "keep me",
  );
  assert.throws(() => store.createTask("../../outside"));
  fs.rmSync(second.cwd, { recursive: true });
  fs.symlinkSync(path.dirname(second.cwd), second.cwd);
  assert.equal(store.taskRoot(second), null);
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

test("discard closes the local session and forgets history without deleting dirty owned worktrees", async (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const repo = path.join(path.dirname(fixture.path), "repo");
  const worktree = path.join(path.dirname(fixture.path), "worktree");
  fs.mkdirSync(repo);
  const git = (...args) =>
    execFileSync("git", ["-C", repo, ...args], { encoding: "utf8" });
  git("init", "--quiet");
  git("config", "user.name", "Test");
  git("config", "user.email", "test@example.com");
  fs.writeFileSync(path.join(repo, "tracked.txt"), "original");
  git("add", ".");
  git("commit", "--quiet", "-m", "initial");
  git("worktree", "add", "--quiet", "-b", "thread-work", worktree);
  fs.writeFileSync(path.join(worktree, "tracked.txt"), "uncommitted change");
  fs.writeFileSync(path.join(worktree, "new.txt"), "untracked work");
  const ref = "refs/open-swe/local/thread-1";
  git("update-ref", ref, "HEAD");
  store.create({ id: "thread-1", cwd: repo });
  store.setWorktree("thread-1", worktree, true);
  store.setCheckpoint("thread-1", { repo, ref, branch: "thread-work" });
  const closed = new Set();
  const source = fs.readFileSync(require.resolve("../build/main.cjs"), "utf8");
  const context = {
    localThreadStore: store,
    localBridges: { close: async (id) => closed.add(`bridge:${id}`) },
    closeThreadTerminals: async (id) => closed.add(`terminal:${id}`),
    deleteRefs,
    removeWorktree,
    repoRoot: async () => repo,
    managedWorktree: () => true,
    console,
  };
  await runInNewContext(
    source.slice(
      source.indexOf("async function discardLocalThread"),
      source.indexOf("function configureDesktopIpc"),
    ) + '\ndiscardLocalThread(localThreadStore.get("thread-1"));',
    context,
  );
  assert.equal(store.get("thread-1"), null);
  assert.deepEqual(closed, new Set(["bridge:thread-1", "terminal:thread-1"]));
  assert.equal(git("for-each-ref", "--format=%(refname)", ref).trim(), "");
  assert.equal(
    fs.readFileSync(path.join(worktree, "tracked.txt"), "utf8"),
    "uncommitted change",
  );
  assert.equal(
    fs.readFileSync(path.join(worktree, "new.txt"), "utf8"),
    "untracked work",
  );
  assert.match(
    git("worktree", "list", "--porcelain"),
    /branch refs\/heads\/thread-work/,
  );
});
