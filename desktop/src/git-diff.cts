const { execFile, execFileSync, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const MAX_FILES = 200;
const MAX_FILE_BYTES = 400_000;
const MAX_CONTENT_BYTES = 16 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 64 * 1024 * 1024;
const MAX_GH_OUTPUT_BYTES = 1024 * 1024;
const CHECKPOINT_NAMESPACE = "refs/open-swe/local";
// `worktree add` checks out the whole tree, which on a large repository takes
// far longer than any other git call here.
const WORKTREE_TIMEOUT_MS = 300_000;

// The project is whatever directory the user picked, so never let its git config
// start helper processes of its own on our behalf.
const HARDENED = [
  "-c",
  "core.fsmonitor=false",
  "-c",
  "core.untrackedCache=false",
];

function git(
  cwd: string,
  args: string[],
  env: NodeJS.ProcessEnv | null = null,
  timeout?: number,
) {
  return new Promise<Buffer>((resolve, reject) => {
    execFile(
      "git",
      [...HARDENED, ...args],
      {
        cwd,
        env: env || process.env,
        encoding: "buffer",
        maxBuffer: MAX_OUTPUT_BYTES,
        timeout,
      },
      (error, stdout) => (error ? reject(error) : resolve(stdout)),
    );
  });
}

/** The branch `origin/HEAD` points at, which is the repository's default. */
async function defaultBranch(cwd) {
  try {
    return (
      text(
        await git(
          cwd,
          ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
          null,
          5_000,
        ),
      ).replace(/^origin\//, "") || null
    );
  } catch {
    return null;
  }
}

/**
 * Local branches, most recently committed first, with the two the picker
 * annotates pulled to the front so they survive the list's scroll area.
 */
async function localBranches(cwd) {
  try {
    const [output, current, fallback] = await Promise.all([
      git(
        cwd,
        [
          "for-each-ref",
          "--format=%(refname:short)",
          "--sort=-committerdate",
          "refs/heads",
        ],
        null,
        5_000,
      ).then(text),
      currentBranch(cwd),
      defaultBranch(cwd),
    ]);
    const refs = (output ? output.split("\n") : []).map((name) => ({
      name,
      current: name === current,
      isDefault: name === fallback,
    }));
    const rank = (ref) => (ref.current ? 0 : ref.isDefault ? 1 : 2);
    return refs.sort((left, right) => rank(left) - rank(right));
  } catch {
    return [];
  }
}

async function checkoutBranch(cwd, branch, create = false) {
  if (typeof branch !== "string" || !branch.trim())
    throw new Error("Branch name is required");
  const name = branch.trim();
  await git(cwd, ["check-ref-format", "--branch", name], null, 5_000);
  await git(
    cwd,
    create ? ["switch", "-c", name] : ["switch", name],
    null,
    30_000,
  );
  return name;
}

/** Check out `baseRef` into its own worktree on a new branch. */
async function addWorktree(repo, worktreePath, branch, baseRef) {
  await git(
    repo,
    ["worktree", "add", "-b", branch, worktreePath, baseRef],
    null,
    WORKTREE_TIMEOUT_MS,
  );
  return worktreePath;
}

/**
 * Re-check out a thread's worktree after its directory disappeared. Git still
 * holds the administrative entry, which makes `worktree add` refuse the path
 * until it is pruned.
 */
async function restoreWorktree(repo, worktreePath, branch) {
  await ok(git(repo, ["worktree", "prune"], null, 30_000));
  await git(
    repo,
    ["worktree", "add", worktreePath, branch],
    null,
    WORKTREE_TIMEOUT_MS,
  );
  return worktreePath;
}

/** Discard a thread's worktree, including anything uncommitted in it. */
async function removeWorktree(repo, worktreePath) {
  const removed = await ok(
    git(repo, ["worktree", "remove", "--force", worktreePath], null, 30_000),
  );
  if (removed) return;
  fs.rmSync(worktreePath, { recursive: true, force: true });
  await ok(git(repo, ["worktree", "prune"], null, 30_000));
}

async function currentBranch(cwd) {
  try {
    return (
      text(
        await git(
          cwd,
          ["symbolic-ref", "--quiet", "--short", "HEAD"],
          null,
          5_000,
        ),
      ) || null
    );
  } catch {
    return null;
  }
}

function count(value) {
  return Number.isInteger(value) && value >= 0 ? value : 0;
}

/** `owner/repo` from an already-validated pull request URL. */
function repoFullNameFromUrl(url: URL) {
  const [owner, repo] = url.pathname.split("/").filter(Boolean);
  return owner && repo ? `${owner}/${repo}` : null;
}

function parsePullRequest(raw) {
  try {
    const value = JSON.parse(raw);
    const url = new URL(value.url);
    const repoFullName = repoFullNameFromUrl(url);
    if (
      !Number.isInteger(value.number) ||
      value.number < 1 ||
      typeof value.title !== "string" ||
      !["OPEN", "CLOSED", "MERGED"].includes(value.state) ||
      typeof value.isDraft !== "boolean" ||
      typeof value.headRefName !== "string" ||
      typeof value.baseRefName !== "string" ||
      !["http:", "https:"].includes(url.protocol) ||
      !repoFullName
    ) {
      return null;
    }
    const login = value.author?.login;
    return {
      number: value.number,
      title: value.title,
      state:
        value.state === "OPEN" && value.isDraft
          ? "draft"
          : value.state.toLowerCase(),
      headRef: value.headRefName,
      baseRef: value.baseRefName,
      url: url.href,
      repoFullName,
      author: typeof login === "string" && login ? login : null,
      authorAvatarUrl: null,
      createdAt: typeof value.createdAt === "string" ? value.createdAt : null,
      diffStats: {
        files: count(value.changedFiles),
        additions: count(value.additions),
        deletions: count(value.deletions),
      },
    };
  } catch {
    return null;
  }
}

async function pullRequest(repo, env, branch = null) {
  try {
    const output = await new Promise<string>((resolve, reject) => {
      execFile(
        "gh",
        [
          "pr",
          "view",
          ...(branch ? [branch] : []),
          "--json",
          "number,title,state,isDraft,headRefName,baseRefName,url,author,createdAt,additions,deletions,changedFiles",
        ],
        {
          cwd: repo,
          env: { ...(env || process.env), GH_PROMPT_DISABLED: "1" },
          encoding: "utf8",
          maxBuffer: MAX_GH_OUTPUT_BYTES,
          timeout: 5_000,
        },
        (error, stdout) => (error ? reject(error) : resolve(stdout)),
      );
    });
    return parsePullRequest(output);
  } catch {
    return null;
  }
}

/** `repo` is the thread's own worktree, so its checkout is the thread's branch. */
async function repositoryMetadata(repo, env) {
  const branch = await currentBranch(repo);
  return { branch, pr: branch ? await pullRequest(repo, env, branch) : null };
}

function gitStdin(cwd, args, input) {
  return new Promise<Buffer>((resolve, reject) => {
    const child = spawn("git", [...HARDENED, ...args], {
      cwd,
      stdio: ["pipe", "pipe", "ignore"],
    });
    const chunks = [];
    child.stdout.on("data", (chunk) => chunks.push(chunk));
    child.on("error", reject);
    child.on("close", () => resolve(Buffer.concat(chunks)));
    child.stdin.end(input);
  });
}

function text(buffer: Buffer) {
  return buffer.toString("utf8").trim();
}

function ok(promise) {
  return promise.then(
    () => true,
    () => false,
  );
}

function checkpointRef(sessionId) {
  return `${CHECKPOINT_NAMESPACE}/${sessionId.replace(/[^A-Za-z0-9._-]/g, "-")}`;
}

async function repoRoot(cwd) {
  try {
    return text(await git(cwd, ["rev-parse", "--show-toplevel"])) || null;
  } catch {
    return null;
  }
}

/** Snapshot the worktree (tracked + untracked, ignoring .gitignore) into a tree oid. */
async function writeWorktreeTree(repo) {
  const gitDir = text(await git(repo, ["rev-parse", "--absolute-git-dir"]));
  const indexFile = path.join(gitDir, `open-swe-index-${randomUUID()}`);
  const env = { ...process.env, GIT_INDEX_FILE: indexFile };
  try {
    const hasHead = await ok(
      git(repo, ["rev-parse", "--verify", "-q", "HEAD"], env),
    );
    await git(repo, ["read-tree", ...(hasHead ? ["HEAD"] : ["--empty"])], env);
    await git(repo, ["add", "-A", "--", "."], env);
    return text(await git(repo, ["write-tree"], env));
  } finally {
    fs.rmSync(indexFile, { force: true });
  }
}

async function captureCheckpoint(repo, ref) {
  const tree = await writeWorktreeTree(repo);
  const hasHead = await ok(git(repo, ["rev-parse", "--verify", "-q", "HEAD"]));
  const commit = text(
    await git(
      repo,
      [
        "commit-tree",
        tree,
        ...(hasHead ? ["-p", "HEAD"] : []),
        "-m",
        "open-swe-local-turn",
      ],
      {
        ...process.env,
        GIT_AUTHOR_NAME: "Open SWE",
        GIT_AUTHOR_EMAIL: "open-swe@users.noreply.github.com",
        GIT_COMMITTER_NAME: "Open SWE",
        GIT_COMMITTER_EMAIL: "open-swe@users.noreply.github.com",
      },
    ),
  );
  await git(repo, ["update-ref", ref, commit]);
  return ref;
}

/** Synchronous so it can also run from Electron's `before-quit`. */
function deleteRefs(repo, refs) {
  for (const ref of refs) {
    try {
      execFileSync("git", ["update-ref", "-d", ref], {
        cwd: repo,
        stdio: "ignore",
      });
    } catch {}
  }
}

/** Checkpoint refs in `repo` that no live session owns any more. */
async function staleRefs(repo, liveRefs) {
  const listed = await git(repo, [
    "for-each-ref",
    "--format=%(refname)",
    CHECKPOINT_NAMESPACE,
  ]).catch(() => Buffer.alloc(0));
  return text(listed)
    .split("\n")
    .filter((ref) => ref && !liveRefs.includes(ref));
}

function parseNumstat(raw) {
  const stats = [];
  for (const record of raw.split("\0")) {
    const parts = record.split("\t");
    if (parts.length !== 3 || !parts[2]) continue;
    stats.push({
      path: parts[2],
      additions: parts[0] === "-" ? null : Number(parts[0]),
      deletions: parts[1] === "-" ? null : Number(parts[1]),
    });
  }
  return stats;
}

function parseNameStatus(raw) {
  const fields = raw.split("\0").filter(Boolean);
  const statuses = new Map();
  for (let i = 0; i + 1 < fields.length; i += 2) {
    const kind = fields[i][0];
    statuses.set(
      fields[i + 1],
      kind === "A" ? "added" : kind === "D" ? "removed" : "modified",
    );
  }
  return statuses;
}

/** Blob sizes for each spec: `null` when the spec does not resolve. */
async function readBlobSizes(repo, specs) {
  const output = await gitStdin(
    repo,
    ["cat-file", "--batch-check"],
    `${specs.join("\n")}\n`,
  );
  return output
    .toString("utf8")
    .split("\n")
    .slice(0, specs.length)
    .map((line) => {
      const fields = line.split(" ");
      return fields.length === 3 && fields[1] === "blob"
        ? Number(fields[2])
        : null;
    });
}

/** Bodies of `specs`, in order, from one `cat-file --batch`. */
async function readBlobBodies(repo, specs) {
  const output = await gitStdin(
    repo,
    ["cat-file", "--batch"],
    `${specs.join("\n")}\n`,
  );
  const bodies = [];
  let at = 0;
  for (let i = 0; i < specs.length; i++) {
    const end = output.indexOf("\n", at);
    if (end < 0) break;
    const header = output.subarray(at, end).toString("utf8").split(" ");
    at = end + 1;
    if (header.length < 3) {
      bodies.push(null);
      continue;
    }
    const size = Number(header[2]);
    bodies.push(output.subarray(at, at + size));
    at += size + 1;
  }
  return bodies;
}

/**
 * Both sides of every changed path, `false` when too large to render. Sizes are
 * checked before any content is read so one huge file cannot balloon the main
 * process, and the whole read stays under a fixed budget.
 */
async function readBlobs(repo, base, head, paths) {
  const specs = paths.flatMap((file) => [`${base}:${file}`, `${head}:${file}`]);
  const sizes = await readBlobSizes(repo, specs);
  const wanted = [];
  let budget = MAX_CONTENT_BYTES;
  sizes.forEach((size, i) => {
    if (size === null || size > MAX_FILE_BYTES || size > budget) return;
    budget -= size;
    wanted.push(i);
  });

  const bodies = wanted.length
    ? await readBlobBodies(
        repo,
        wanted.map((i) => specs[i]),
      )
    : [];
  const blobs = sizes.map((size) => (size === null ? null : false));
  wanted.forEach((specIndex, i) => {
    blobs[specIndex] = bodies[i] ?? null;
  });
  return new Map(
    paths.map((file, i) => [
      file,
      { base: blobs[i * 2], head: blobs[i * 2 + 1] },
    ]),
  );
}

function decode(blob: Buffer | false | null) {
  if (blob === false) return [null, true];
  if (!Buffer.isBuffer(blob)) return [null, false];
  const content = blob.toString("utf8");
  return content.includes("\u0000") ? [null, true] : [content, false];
}

/**
 * Files changed between `base` and `head`, shaped like the cloud turn diff.
 * `head` defaults to the live worktree.
 */
async function readDiff(repo, base, headish = null) {
  const head = headish ?? (await writeWorktreeTree(repo));
  const range = ["--no-renames", "--no-ext-diff", "--no-textconv", base, head];
  const numstat = (
    await git(repo, ["diff", "--numstat", "-z", ...range])
  ).toString("utf8");
  const nameStatus = (
    await git(repo, ["diff", "--name-status", "-z", ...range])
  ).toString("utf8");
  const stats = parseNumstat(numstat);
  const statuses = parseNameStatus(nameStatus);
  const shown = stats.slice(0, MAX_FILES);
  const blobs = shown.length
    ? await readBlobs(
        repo,
        base,
        head,
        shown.map((stat) => stat.path),
      )
    : new Map();

  return {
    status: "ready",
    truncated: stats.length > MAX_FILES,
    files: shown.map((stat) => {
      const sides = blobs.get(stat.path) || {};
      const [originalContent, badBase] = decode(sides.base);
      const [modifiedContent, badHead] = decode(sides.head);
      return {
        path: stat.path,
        previousPath: null,
        status: statuses.get(stat.path) || "modified",
        additions: stat.additions ?? 0,
        deletions: stat.deletions ?? 0,
        originalContent,
        modifiedContent,
        unrenderable: stat.additions === null || badBase || badHead,
      };
    }),
  };
}

/** First ref spec that resolves to a commit, preferring the pushed remote. */
/** A branch name safe to pass as a positional argument, or null. */
async function validBranchName(repo, branch) {
  if (typeof branch !== "string" || !branch.trim()) return null;
  const name = branch.trim();
  if (name.startsWith("-")) return null;
  try {
    await git(repo, ["check-ref-format", "--branch", name], null, 5_000);
  } catch {
    return null;
  }
  return name;
}

async function resolveBaseRef(repo, baseRef) {
  const name = await validBranchName(repo, baseRef);
  if (!name) return null;
  for (const spec of [`origin/${name}`, name]) {
    try {
      return text(
        await git(
          repo,
          ["rev-parse", "--verify", "-q", `${spec}^{commit}`],
          null,
          5_000,
        ),
      );
    } catch {}
  }
  return null;
}

/**
 * What `headRef` has *committed* on top of `baseRef` — the pull request's own
 * content. `headRef` defaults to the checkout when the thread's own branch is
 * unknown.
 */
async function readBranchDiff(repo, baseRef, headRef = null) {
  const missing = { status: "missing", files: [], truncated: false };
  const base = await resolveBaseRef(repo, baseRef);
  if (!base) return missing;
  const named = await validBranchName(repo, headRef);
  let head = "HEAD";
  if (named) {
    try {
      head = text(
        await git(
          repo,
          ["rev-parse", "--verify", "-q", `${named}^{commit}`],
          null,
          5_000,
        ),
      );
    } catch {
      return missing;
    }
    if (!head) return missing;
  } else if (headRef) {
    return missing;
  }
  const mergeBase = text(
    await git(repo, ["merge-base", base, head], null, 10_000),
  );
  if (!mergeBase) return missing;
  return readDiff(repo, mergeBase, head);
}

module.exports = {
  addWorktree,
  captureCheckpoint,
  readBranchDiff,
  checkoutBranch,
  checkpointRef,
  currentBranch,
  defaultBranch,
  localBranches,
  deleteRefs,
  parsePullRequest,
  readDiff,
  removeWorktree,
  repoRoot,
  repositoryMetadata,
  restoreWorktree,
  staleRefs,
  validBranchName,
};
