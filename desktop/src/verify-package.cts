const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { listPackage } = require("@electron/asar");

function isForbiddenRuntimePath(value) {
  const name = path.basename(value);
  return (
    /\.(?:py|pyc|pyo)$/i.test(name) ||
    /^python(?:\d+(?:\.\d+)*)?(?:\.exe)?$/i.test(name) ||
    /^uv(?:\.exe)?$/i.test(name) ||
    name === "pyvenv.cfg"
  );
}

function forbiddenRuntimeFiles(root) {
  const found = [];
  const archives = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        visit(absolute);
        continue;
      }
      if (entry.name === "app.asar") archives.push(absolute);
      if (isForbiddenRuntimePath(entry.name)) {
        found.push(path.relative(root, absolute));
      }
    }
  };
  visit(root);
  for (const archive of archives) {
    for (const entry of listPackage(archive, { isPack: false })) {
      if (isForbiddenRuntimePath(entry)) {
        found.push(`${path.relative(root, archive)}:${entry}`);
      }
    }
  }
  return found;
}

exports.isForbiddenRuntimePath = isForbiddenRuntimePath;
exports.forbiddenRuntimeFiles = forbiddenRuntimeFiles;

function verifyLocalBackend(localBackendRoot) {
  const entrypoint = path.join(localBackendRoot, "dist", "bin.js");
  if (!fs.existsSync(entrypoint)) {
    throw new Error(`Desktop package is missing local backend file: ${entrypoint}`);
  }

  // The packaged server runs on the Electron binary in Node mode, which is what
  // resolves the dependency graph at runtime — so that is what has to resolve it
  // here too. No Node is bundled beside the app any more.
  const result = spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "--eval",
      "await import('@langchain/langgraph-api')",
    ],
    {
      cwd: localBackendRoot,
      encoding: "utf8",
      env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
    },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `Desktop local backend dependencies are incomplete:\n${result.stderr || result.stdout}`,
    );
  }
}

exports.verifyLocalBackend = verifyLocalBackend;

exports.default = async function verifyPackage(context) {
  const forbidden = forbiddenRuntimeFiles(context.appOutDir);
  if (forbidden.length > 0) {
    throw new Error(
      `Desktop package contains forbidden Python or uv files:\n${forbidden.join("\n")}`,
    );
  }

  const productName = context.packager.appInfo.productFilename;
  const resourcesRoot =
    context.electronPlatformName === "darwin"
      ? path.join(context.appOutDir, `${productName}.app`, "Contents", "Resources")
      : path.join(context.appOutDir, "resources");
  verifyLocalBackend(path.join(resourcesRoot, "local-backend"));
};
