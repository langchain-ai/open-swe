const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  downloadFiles,
  resolveWorkingDirectory,
  runCommand,
  uploadFiles,
} = require("../build/local-runner.cjs");

function project() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-runner-"));
}

const allowAll = (cwd) => cwd;
const allowNone = () => null;
const knowsThread = () => true;

test("runs a frame only in a project the user approved", () => {
  const cwd = project();
  const frame = { device_id: "abc", thread_id: "t1", project_path: cwd };
  assert.equal(
    resolveWorkingDirectory(frame, { registeredProject: allowAll, deviceId: "abc", knowsThread }),
    cwd,
  );
  assert.equal(
    resolveWorkingDirectory(frame, { registeredProject: allowNone, deviceId: "abc", knowsThread }),
    null,
  );
});

test("refuses a frame addressed to another device", () => {
  const cwd = project();
  assert.equal(
    resolveWorkingDirectory(
      { device_id: "other", thread_id: "t1", project_path: cwd },
      { registeredProject: allowAll, deviceId: "abc", knowsThread },
    ),
    null,
  );
});

test("refuses a thread this machine has no checkpoint for", () => {
  const cwd = project();
  assert.equal(
    resolveWorkingDirectory(
      { device_id: "abc", thread_id: "unknown", project_path: cwd },
      { registeredProject: allowAll, deviceId: "abc", knowsThread: () => false },
    ),
    null,
  );
});

test("refuses a project path that is not a directory", () => {
  const cwd = project();
  const file = path.join(cwd, "file.txt");
  fs.writeFileSync(file, "x");
  assert.equal(
    resolveWorkingDirectory(
      { device_id: "abc", thread_id: "t1", project_path: file },
      { registeredProject: allowAll, deviceId: "abc", knowsThread },
    ),
    null,
  );
});

test("reports the command's output and exit code", async () => {
  const cwd = project();
  const ok = await runCommand("printf hello", cwd, 30);
  assert.equal(ok.output, "hello");
  assert.equal(ok.exit_code, 0);
  const failed = await runCommand("exit 3", cwd, 30);
  assert.equal(failed.exit_code, 3);
});

test("kills a command that outlasts its timeout", async () => {
  const cwd = project();
  const result = await runCommand("sleep 5", cwd, 1);
  assert.notEqual(result.exit_code, 0);
});

test("confines uploads and downloads to the project", async () => {
  const cwd = project();
  const [inside, outside] = await uploadFiles(
    {
      files: [
        { path: "notes/a.txt", content: Buffer.from("hi").toString("base64") },
        { path: "../escape.txt", content: Buffer.from("no").toString("base64") },
      ],
    },
    cwd,
  );
  assert.deepEqual(inside, {});
  assert.equal(outside.error, "invalid_path");
  assert.equal(fs.readFileSync(path.join(cwd, "notes/a.txt"), "utf8"), "hi");

  const [read, escaped, missing] = await downloadFiles(
    { paths: ["notes/a.txt", "../escape.txt", "nope.txt"] },
    cwd,
  );
  assert.equal(Buffer.from(read.content, "base64").toString("utf8"), "hi");
  assert.equal(escaped.error, "invalid_path");
  assert.equal(missing.error, "file_not_found");
});
