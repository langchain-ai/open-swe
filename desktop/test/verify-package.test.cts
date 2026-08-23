const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  forbiddenRuntimeFiles,
  isForbiddenRuntimePath,
  verifyLocalBackend,
} = require("../build/verify-package.cjs");

test("recognizes forbidden runtime paths inside archives", () => {
  assert.equal(isForbiddenRuntimePath("/node_modules/tool/helper.py"), true);
  assert.equal(isForbiddenRuntimePath("/runtime/bin/python3.12"), true);
  assert.equal(isForbiddenRuntimePath("/runtime/bin/uv"), true);
  assert.equal(isForbiddenRuntimePath("/dist/bin.js"), false);
});

test("rejects Python and uv files from packaged resources", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-package-policy-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "nested"));
  fs.writeFileSync(path.join(root, "nested", "server.js"), "");
  fs.writeFileSync(path.join(root, "nested", "helper.py"), "");
  fs.writeFileSync(path.join(root, "uv"), "");

  assert.deepEqual(forbiddenRuntimeFiles(root).sort(), [
    "nested/helper.py",
    "uv",
  ]);
});

test("local packaging disables macOS signing identity discovery", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, "../package.json"), "utf8"),
  );
  assert.match(manifest.scripts.pack, /tsx scripts\/package-local\.ts/);
  assert.match(manifest.scripts.dist, /tsx scripts\/package-local\.ts/);

  const source = fs.readFileSync(
    path.resolve(__dirname, "../scripts/package-local.ts"),
    "utf8",
  );
  assert.match(source, /CSC_IDENTITY_AUTO_DISCOVERY:\s*"false"/);
});

test("rejects a packaged local backend without its entrypoint", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-local-backend-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "dist"), { recursive: true });

  assert.throws(
    () => verifyLocalBackend(root),
    /missing local backend file.*dist\/bin\.js/,
  );
});

test("rejects a packaged local backend without runtime dependencies", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-local-backend-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "dist"), { recursive: true });
  fs.writeFileSync(path.join(root, "dist", "bin.js"), "");

  assert.throws(
    () => verifyLocalBackend(root),
    /local backend dependencies are incomplete/,
  );
});
