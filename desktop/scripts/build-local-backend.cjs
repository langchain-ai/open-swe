const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const desktopRoot = path.resolve(__dirname, "..");
const repositoryRoot = path.resolve(desktopRoot, "..");
const outputRoot = path.join(desktopRoot, "resources", "local-backend");
const stagingRoot = fs.mkdtempSync(
  path.join(os.tmpdir(), "open-swe-local-backend-"),
);
const runtimeRoot = path.join(outputRoot, "runtime");
const uv = process.env.OPEN_SWE_UV_COMMAND || "uv";
const pythonVersion = process.env.OPEN_SWE_LOCAL_PYTHON_VERSION || "3.12";

function run(args) {
  const result = spawnSync(uv, args, {
    cwd: repositoryRoot,
    stdio: "inherit",
    env: { ...process.env, UV_NO_PROGRESS: "1" },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}

fs.rmSync(outputRoot, { recursive: true, force: true });
fs.mkdirSync(outputRoot, { recursive: true });
try {
  run([
    "python",
    "install",
    pythonVersion,
    "--install-dir",
    stagingRoot,
    "--no-bin",
  ]);
  const installed = fs
    .readdirSync(stagingRoot, { withFileTypes: true })
    .find((entry) => entry.isDirectory() && !entry.name.startsWith("."));
  if (!installed) throw new Error("uv did not install a Python runtime");
  fs.renameSync(path.join(stagingRoot, installed.name), runtimeRoot);
  const python = path.join(
    runtimeRoot,
    process.platform === "win32" ? "python.exe" : "bin/python3",
  );
  const requirements = path.join(stagingRoot, "requirements.txt");
  run([
    "export",
    "--locked",
    "--no-dev",
    "--no-emit-project",
    "--no-hashes",
    "--output-file",
    requirements,
  ]);
  const result = spawnSync(
    uv,
    [
      "pip",
      "install",
      "--python",
      python,
      "--break-system-packages",
      "--no-cache",
      "--compile-bytecode",
      "--requirements",
      requirements,
      repositoryRoot,
    ],
    {
      cwd: repositoryRoot,
      stdio: "inherit",
      env: { ...process.env, UV_NO_PROGRESS: "1" },
    },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
  fs.cpSync(
    path.join(repositoryRoot, "langgraph.desktop.json"),
    path.join(outputRoot, "langgraph.json"),
  );
} finally {
  fs.rmSync(stagingRoot, { recursive: true, force: true });
}
