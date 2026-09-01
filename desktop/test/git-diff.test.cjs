const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  addWorktree,
  captureCheckpoint,
  checkpointRef,
  currentBranch,
  localBranches,
  parsePullRequest,
  readBranchDiff,
  readDiff,
  removeWorktree,
  repoRoot,
  restoreWorktree,
} = require("../build/git-diff.cjs");

function git(cwd, args) {
  execFileSync("git", args, { cwd, stdio: "ignore" });
}

test("normalizes validated pull request metadata", () => {
  const pr = parsePullRequest(
    JSON.stringify({
      number: 12,
      title: "Draft change",
      state: "OPEN",
      isDraft: true,
      headRefName: "feature",
      baseRefName: "main",
      url: "https://github.com/example/repo/pull/12",
      author: { login: "octocat" },
      createdAt: "2026-08-20T00:00:00Z",
      changedFiles: 3,
      additions: 10,
      deletions: 2,
    }),
  );
  assert.deepEqual(pr, {
    number: 12,
    title: "Draft change",
    state: "draft",
    headRef: "feature",
    baseRef: "main",
    url: "https://github.com/example/repo/pull/12",
    repoFullName: "example/repo",
    author: "octocat",
    authorAvatarUrl: null,
    createdAt: "2026-08-20T00:00:00Z",
    diffStats: { files: 3, additions: 10, deletions: 2 },
  });
  assert.equal(
    parsePullRequest(
      JSON.stringify({
        number: 12,
        title: "Closed draft",
        state: "CLOSED",
        isDraft: true,
        headRefName: "feature",
        baseRefName: "main",
        url: "https://github.com/example/repo/pull/12",
      }),
    ).state,
    "closed",
  );
  assert.equal(parsePullRequest('{"url":"javascript:alert(1)"}'), null);
});

test("diffs the worktree against a session checkpoint", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-git-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  git(dir, ["init", "-q", "-b", "main"]);
  git(dir, ["config", "user.email", "test@example.com"]);
  git(dir, ["config", "user.name", "Test"]);
  fs.writeFileSync(path.join(dir, "kept.txt"), "one\ntwo\n");
  fs.writeFileSync(path.join(dir, "gone.txt"), "bye\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-qm", "init"]);

  const repo = await repoRoot(dir);
  assert.equal(await currentBranch(dir), "main");
  git(dir, ["branch", "feature"]);
  // The checked-out branch is annotated and sorts ahead of the rest.
  assert.deepEqual(await localBranches(dir), [
    { name: "main", current: true, isDefault: false, worktreePath: null },
    { name: "feature", current: false, isDefault: false, worktreePath: null },
  ]);
  const ref = checkpointRef("session-id");
  await captureCheckpoint(repo, ref);

  fs.writeFileSync(path.join(dir, "kept.txt"), "one\ntwo\nthree\n");
  fs.writeFileSync(path.join(dir, "added.txt"), "fresh\n");
  fs.writeFileSync(path.join(dir, "binary.dat"), Buffer.from([0, 1, 2, 0]));
  fs.writeFileSync(path.join(dir, "huge.txt"), "x".repeat(500_000));
  fs.rmSync(path.join(dir, "gone.txt"));

  const diff = await readDiff(repo, ref);
  assert.equal(diff.status, "ready");
  assert.equal(diff.truncated, false);
  assert.deepEqual(
    diff.files.map((file) => [
      file.path,
      file.status,
      file.additions,
      file.deletions,
    ]),
    [
      ["added.txt", "added", 1, 0],
      ["binary.dat", "added", 0, 0],
      ["gone.txt", "removed", 0, 1],
      ["huge.txt", "added", 1, 0],
      ["kept.txt", "modified", 1, 0],
    ],
  );
  const kept = diff.files.find((file) => file.path === "kept.txt");
  assert.equal(kept.originalContent, "one\ntwo\n");
  assert.equal(kept.modifiedContent, "one\ntwo\nthree\n");
  assert.equal(kept.unrenderable, false);
  assert.equal(
    diff.files.find((file) => file.path === "binary.dat").unrenderable,
    true,
  );
  assert.equal(
    diff.files.find((file) => file.path === "gone.txt").modifiedContent,
    null,
  );

  // Oversized blobs are never read into memory, only reported.
  const huge = diff.files.find((file) => file.path === "huge.txt");
  assert.equal(huge.unrenderable, true);
  assert.equal(huge.modifiedContent, null);
});

test("branch diff reports only what the branch committed", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-git-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  git(dir, ["init", "-q", "-b", "main"]);
  git(dir, ["config", "user.email", "test@example.com"]);
  git(dir, ["config", "user.name", "Test"]);
  fs.writeFileSync(path.join(dir, "models.ts"), "one\n");
  fs.writeFileSync(path.join(dir, "search.ts"), "search\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-qm", "init"]);

  const repo = await repoRoot(dir);
  git(dir, ["switch", "-q", "-c", "feature"]);
  fs.writeFileSync(path.join(dir, "models.ts"), "one\ntwo\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-qm", "feature work"]);

  // Uncommitted noise in the checkout is not part of the branch's diff.
  fs.writeFileSync(path.join(dir, "search.ts"), "search\nelsewhere\n");
  fs.writeFileSync(path.join(dir, "stray.txt"), "stray\n");

  const diff = await readBranchDiff(repo, "main");
  assert.equal(diff.status, "ready");
  assert.deepEqual(
    diff.files.map((file) => [file.path, file.status, file.additions]),
    [["models.ts", "modified", 1]],
  );

  assert.equal(
    (await readBranchDiff(repo, "no-such-branch")).status,
    "missing",
  );
  assert.equal(
    (await readBranchDiff(repo, "--upload-pack=touch")).status,
    "missing",
  );

  // The thread's branch is reported even while another one holds the checkout.
  git(dir, ["switch", "-q", "main"]);
  fs.writeFileSync(path.join(dir, "search.ts"), "search\nmain work\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-qm", "main work"]);

  const fromElsewhere = await readBranchDiff(repo, "main", "feature");
  assert.deepEqual(
    fromElsewhere.files.map((file) => file.path),
    ["models.ts"],
  );
  assert.equal(
    (await readBranchDiff(repo, "main", "no-such-branch")).status,
    "missing",
  );
  assert.equal(
    (await readBranchDiff(repo, "main", "--upload-pack=touch")).status,
    "missing",
  );
});

test("a thread worktree survives its directory being deleted", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-git-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  git(dir, ["init", "-q", "-b", "main"]);
  git(dir, ["config", "user.email", "test@example.com"]);
  git(dir, ["config", "user.name", "Test"]);
  fs.writeFileSync(path.join(dir, "kept.txt"), "one\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-qm", "init"]);

  const worktree = path.join(dir, "..", path.basename(dir) + "-wt");
  t.after(() => fs.rmSync(worktree, { recursive: true, force: true }));
  await addWorktree(dir, worktree, "open-swe/local-abc12345", "main");
  assert.equal(await currentBranch(worktree), "open-swe/local-abc12345");
  // The picker moves a thread by following where a ref is checked out.
  assert.equal(
    (await localBranches(dir)).find(
      (ref) => ref.name === "open-swe/local-abc12345",
    ).worktreePath,
    fs.realpathSync(worktree),
  );

  fs.rmSync(worktree, { recursive: true, force: true });
  await restoreWorktree(dir, worktree, "open-swe/local-abc12345");
  assert.equal(fs.existsSync(path.join(worktree, "kept.txt")), true);

  // Uncommitted work is discarded with the worktree, and removal is idempotent.
  fs.writeFileSync(path.join(worktree, "kept.txt"), "dirty\n");
  await removeWorktree(dir, worktree);
  await removeWorktree(dir, worktree);
  assert.equal(fs.existsSync(worktree), false);
});
