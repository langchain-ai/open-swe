const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

const MAX_FILE_BYTES = 1024 * 1024;

function ignoredPaths(root: string, paths: string[]) {
  if (paths.length === 0) return Promise.resolve(new Set<string>());
  return new Promise<Set<string>>((resolve) => {
    const child = spawn("git", ["-C", root, "check-ignore", "-z", "--stdin"]);
    const chunks: Buffer[] = [];
    child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
    child.on("error", () => resolve(new Set()));
    child.on("close", () =>
      resolve(new Set(Buffer.concat(chunks).toString().split("\0"))),
    );
    child.stdin.end(`${paths.join("\0")}\0`);
  });
}

async function listDirectory(root: string, target: string, relative: string) {
  const children = await fs.readdir(target, { withFileTypes: true });
  const entries = children
    .filter(
      (child) =>
        child.name !== ".git" && (child.isDirectory() || child.isFile()),
    )
    .map((child) => ({
      path: relative ? `${relative}/${child.name}` : child.name,
      kind: child.isDirectory() ? "directory" : "file",
    }));
  const ignored = await ignoredPaths(
    root,
    entries.map((entry) => entry.path),
  );
  return {
    kind: "directory",
    entries: entries.map((entry) => ({
      ...entry,
      ignored: ignored.has(entry.path),
    })),
  };
}

async function readFile(target: string, size: number) {
  const handle = await fs.open(target, "r");
  try {
    const buffer = Buffer.alloc(Math.min(size, MAX_FILE_BYTES));
    await handle.read(buffer, 0, buffer.length, 0);
    const binary = buffer.includes(0);
    return {
      kind: "file",
      contents: binary ? "" : buffer.toString("utf8"),
      binary,
      truncated: size > MAX_FILE_BYTES,
      size,
    };
  } finally {
    await handle.close();
  }
}

/** Lists a directory or reads a file inside `root`, never escaping it or entering .git. */
async function readWorkspacePath(root: string, relativePath: unknown) {
  if (typeof relativePath !== "string") throw new Error("Invalid path");
  const realRoot = await fs.realpath(root);
  const relative = relativePath.replace(/^\/+|\/+$/g, "");
  const target = await fs.realpath(path.resolve(realRoot, relative));
  const inside = path.relative(realRoot, target);
  if (
    inside === ".." ||
    inside.startsWith(`..${path.sep}`) ||
    path.isAbsolute(inside) ||
    inside.split(path.sep).includes(".git")
  ) {
    throw new Error("Path must be inside the workspace and outside .git.");
  }
  const stat = await fs.stat(target);
  if (stat.isDirectory()) return listDirectory(realRoot, target, relative);
  return readFile(target, stat.size);
}

module.exports = { readWorkspacePath };
