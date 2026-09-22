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
const pythonVersion = process.env.OPEN_SWE_LOCAL_PYTHON_VERSION || "3.14";

function run(args) {
  const result = spawnSync(uv, args, {
    cwd: repositoryRoot,
    stdio: "inherit",
    env: { ...process.env, UV_NO_PROGRESS: "1" },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}

// githubkit-schemas always ships every REST API version (thousands of files each), but
// the backend only requests GITHUB_API_VERSION from agent/github/sdk.py.
const githubkitSchemaDirs = new Set(["core", "v2022_11_28"]);

function pruneGithubkitSchemas(python) {
  const located = spawnSync(
    python,
    ["-c", "import githubkit_schemas; print(githubkit_schemas.__path__[0])"],
    { encoding: "utf8" },
  );
  if (located.error) throw located.error;
  if (located.status !== 0) {
    throw new Error(`Could not locate githubkit_schemas: ${located.stderr}`);
  }
  const packageRoot = located.stdout.trim();
  for (const entry of fs.readdirSync(packageRoot, { withFileTypes: true })) {
    if (entry.isDirectory() && !githubkitSchemaDirs.has(entry.name)) {
      fs.rmSync(path.join(packageRoot, entry.name), {
        recursive: true,
        force: true,
      });
    }
  }
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
  pruneGithubkitSchemas(python);
  fs.cpSync(
    path.join(repositoryRoot, "langgraph.desktop.json"),
    path.join(outputRoot, "langgraph.json"),
  );
} finally {
  fs.rmSync(stagingRoot, { recursive: true, force: true });
}
