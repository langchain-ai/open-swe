const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  addProject,
  linkProject,
  readProjects,
  removeProject,
} = require("../build/project-store.cjs");

test("persists, deduplicates, and removes projects", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-projects-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const storePath = path.join(root, "projects.json");
  const selectedPath = path.join(root, "example");
  fs.mkdirSync(selectedPath);
  const cwd = fs.realpathSync(selectedPath);

  assert.deepEqual(addProject(storePath, selectedPath, 123), {
    cwd,
    name: "example",
    addedAt: 123,
    projectId: null,
  });
  assert.deepEqual(addProject(storePath, selectedPath, 456), {
    cwd,
    name: "example",
    addedAt: 123,
    projectId: null,
  });
  assert.deepEqual(readProjects(storePath), [
    { cwd, name: "example", addedAt: 123, projectId: null },
  ]);
  const projectId = "01a1a2f2-9b94-7691-a2e9-c45c1cd5d66e";
  assert.throws(() => linkProject(storePath, cwd, "../elsewhere"));
  linkProject(storePath, cwd, projectId);
  assert.equal(readProjects(storePath)[0].projectId, projectId);
  linkProject(storePath, cwd, null);
  assert.equal(readProjects(storePath)[0].projectId, null);
  assert.equal(removeProject(storePath, cwd), true);
  assert.deepEqual(readProjects(storePath), []);
  assert.equal(removeProject(storePath, cwd), false);
});

test("drops projects whose directories no longer exist", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-projects-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const storePath = path.join(root, "projects.json");
  const selectedPath = path.join(root, "removed");
  fs.mkdirSync(selectedPath);

  addProject(storePath, selectedPath);
  fs.rmSync(selectedPath, { recursive: true });

  assert.deepEqual(readProjects(storePath), []);
});
