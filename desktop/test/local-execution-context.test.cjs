const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { LocalExecutionContext } = require("../src/local-execution-context.cjs");

test("persists execution context across desktop restarts", (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-context-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const storagePath = path.join(directory, "context.json");
  const cwd = path.join(directory, "repo");
  const context = new LocalExecutionContext(storagePath);

  context.create({
    id: "thread-1",
    cwd,
    prompt: "continue",
    images: [],
    skills: [],
    modelId: "openai:test",
    effort: "high",
    managedWorktree: true,
  });
  context.setCheckpoint("thread-1", {
    repo: cwd,
    ref: "abc123",
    branch: "open-swe/thread-1",
  });

  const restored = new LocalExecutionContext(storagePath).get("thread-1");

  assert.deepEqual(restored, {
    id: "thread-1",
    cwd,
    modelId: "openai:test",
    effort: "high",
    checkpoint: {
      repo: cwd,
      ref: "abc123",
      branch: "open-swe/thread-1",
    },
    managedWorktree: true,
    pending: { prompt: "continue", images: [], skills: [] },
  });
  assert.equal(fs.statSync(storagePath).mode & 0o777, 0o600);
});

test("ignores invalid persisted execution contexts", (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-context-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const storagePath = path.join(directory, "context.json");
  fs.writeFileSync(
    storagePath,
    JSON.stringify([
      { id: "relative", cwd: "../outside" },
      { id: "missing-cwd" },
    ]),
  );

  const context = new LocalExecutionContext(storagePath);

  assert.equal(context.get("relative"), null);
  assert.equal(context.get("missing-cwd"), null);
});
