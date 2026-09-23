const { git, gitStdin, ok } = require("./git-diff.cjs");
const fs = require("node:fs/promises");
const path = require("node:path");

const MAX_FILE_BYTES = 1024 * 1024;
const MAX_INDEX_PATHS = 20000;

async function ignoredPaths(root: string, paths: string[]) {
  if (paths.length === 0) return new Set<string>();
  const output = await gitStdin(
    root,
    ["check-ignore", "-z", "--stdin"],
    `${paths.join("\0")}\0`,
  );
  return new Set<string>(output.toString().split("\0"));
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

async function gitPaths(root: string, flags: string[]) {
  const output = await git(root, ["ls-files", "-z", ...flags]);
  return output.toString().split("\0").filter(Boolean);
}

async function walkFiles(root: string) {
  const paths: string[] = [];
  const directories = [""];
  for (
    let index = 0;
    index < directories.length && paths.length < MAX_INDEX_PATHS;
    index++
  ) {
    const relative = directories[index];
    const children = await fs.readdir(path.join(root, relative), {
      withFileTypes: true,
    });
    for (const child of children) {
      if (child.name === ".git") continue;
      const childPath = relative ? `${relative}/${child.name}` : child.name;
      if (child.isDirectory()) directories.push(childPath);
      else if (child.isFile()) paths.push(childPath);
    }
  }
  return paths;
}

/** Every non-ignored file under `root`, for search; non-Git folders are walked. */
async function listWorkspaceFiles(root: string) {
  if (!(await ok(git(root, ["rev-parse", "--is-inside-work-tree"]))))
    return { paths: (await walkFiles(root)).slice(0, MAX_INDEX_PATHS) };
  // `--cached` keeps tracked files deleted from the working tree until the deletion is staged.
  const [paths, deleted] = await Promise.all([
    gitPaths(root, ["--cached", "--others", "--exclude-standard"]),
    gitPaths(root, ["--deleted"]),
  ]);
  const removed = new Set(deleted);
  return {
    paths: paths.filter((p) => !removed.has(p)).slice(0, MAX_INDEX_PATHS),
  };
}

module.exports = { listWorkspaceFiles, readWorkspacePath };
