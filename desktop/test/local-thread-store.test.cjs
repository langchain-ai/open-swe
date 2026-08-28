const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { LocalThreadStore } = require("../src/local-thread-store.cjs");

function temporaryStore(t) {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-local-threads-"),
  );
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  let now = 100;
  let nextId = 0;
  return {
    path: path.join(root, "threads.json"),
    create: () =>
      new LocalThreadStore(path.join(root, "threads.json"), {
        now: () => ++now,
        uuid: () => `thread-${++nextId}`,
      }),
  };
}

test("persists a prompt until it is acknowledged", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "  fix the tests  ",
    images: [{ base64: "aW1n", mimeType: "image/png", fileName: "bug.png" }],
    modelId: "anthropic:test",
    effort: "high",
    skills: [
      {
        name: "review",
        description: "Review changes",
        instructions: "Be concise.",
      },
      { name: "../bad", description: "Invalid", instructions: "Ignore." },
    ],
  });
  assert.equal(thread.title, "fix the tests");
  assert.equal(fs.statSync(fixture.path).mode & 0o777, 0o600);
  assert.deepEqual(store.pendingPrompt(thread.id), {
    prompt: "  fix the tests  ",
    images: [
      {
        kind: "image",
        base64: "aW1n",
        mimeType: "image/png",
        fileName: "bug.png",
      },
    ],
    skills: [
      {
        name: "review",
        description: "Review changes",
        instructions: "Be concise.",
      },
    ],
  });
  assert.deepEqual(
    store.pendingPrompt(thread.id),
    store.pendingPrompt(thread.id),
  );
  store.clearPrompt(thread.id);
  assert.equal(store.pendingPrompt(thread.id), null);
  const restored = fixture.create().get(thread.id);
  assert.equal(restored.pending, null);
  assert.equal(restored.modelId, "anthropic:test");
});

test("keeps threads in creation order when an older thread is updated", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const older = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "older",
  });
  const newer = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "newer",
  });

  store.update(older.id, { title: "older updated" });

  assert.deepEqual(
    store.list().map((thread) => thread.id),
    [newer.id, older.id],
  );
});

test("retains checkpoint refs until deletion", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "work",
  });
  store.setCheckpoint(thread.id, {
    repo: path.resolve("/tmp/project"),
    ref: "refs/open-swe/local/thread-1",
    branch: "feature",
  });
  assert.equal(store.get(thread.id).checkpoint.branch, "feature");
  store.update(thread.id, { viewed: false });

  const restored = fixture.create();
  assert.equal(restored.get(thread.id).viewed, false);
  assert.equal(
    restored.get(thread.id).checkpoint.ref,
    "refs/open-swe/local/thread-1",
  );
  assert.equal(
    restored.delete(thread.id).checkpoint.ref,
    "refs/open-swe/local/thread-1",
  );
  assert.equal(restored.get(thread.id), null);
});

test("persists archived state without reordering the thread", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "work",
  });
  assert.equal(thread.archived, false);

  const archived = store.update(thread.id, { archived: true });
  assert.equal(archived.archived, true);
  // Archiving is not an edit, so it must not bump the sidebar's sort key.
  assert.equal(archived.updatedAt, thread.updatedAt);

  const restored = fixture.create();
  assert.equal(restored.get(thread.id).archived, true);
  assert.equal(restored.update(thread.id, { archived: false }).archived, false);
});

test("rejects a non-boolean archived patch", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "work",
  });
  assert.throws(() => store.update(thread.id, { archived: "yes" }), {
    message: "Invalid archived state",
  });
});

test("reads a record written before archived existed as unarchived", (t) => {
  const fixture = temporaryStore(t);
  const store = fixture.create();
  const thread = store.create({
    cwd: path.resolve("/tmp/project"),
    prompt: "work",
  });
  const raw = JSON.parse(fs.readFileSync(fixture.path, "utf8"));
  for (const record of raw.threads ?? raw) delete record.archived;
  fs.writeFileSync(fixture.path, JSON.stringify(raw));

  assert.equal(fixture.create().get(thread.id).archived, false);
});
