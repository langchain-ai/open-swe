const { execFile, execFileSync, spawn } = require("node:child_process")
const fs = require("node:fs")
const path = require("node:path")
const { randomUUID } = require("node:crypto")

const MAX_FILES = 200
const MAX_FILE_BYTES = 400_000
const MAX_OUTPUT_BYTES = 64 * 1024 * 1024
const CHECKPOINT_NAMESPACE = "refs/open-swe/local"

function git(cwd, args, env) {
  return new Promise((resolve, reject) => {
    execFile(
      "git",
      args,
      { cwd, env: env || process.env, encoding: "buffer", maxBuffer: MAX_OUTPUT_BYTES },
      (error, stdout) => (error ? reject(error) : resolve(stdout))
    )
  })
}

function gitStdin(cwd, args, input) {
  return new Promise((resolve, reject) => {
    const child = spawn("git", args, { cwd, stdio: ["pipe", "pipe", "ignore"] })
    const chunks = []
    child.stdout.on("data", (chunk) => chunks.push(chunk))
    child.on("error", reject)
    child.on("close", () => resolve(Buffer.concat(chunks)))
    child.stdin.end(input)
  })
}

function text(buffer) {
  return buffer.toString("utf8").trim()
}

function ok(promise) {
  return promise.then(
    () => true,
    () => false
  )
}

function checkpointRef(sessionId) {
  return `${CHECKPOINT_NAMESPACE}/${sessionId.replace(/[^A-Za-z0-9._-]/g, "-")}`
}

async function repoRoot(cwd) {
  try {
    return text(await git(cwd, ["rev-parse", "--show-toplevel"])) || null
  } catch {
    return null
  }
}

/** Snapshot the worktree (tracked + untracked, ignoring .gitignore) into a tree oid. */
async function writeWorktreeTree(repo) {
  const gitDir = text(await git(repo, ["rev-parse", "--absolute-git-dir"]))
  const indexFile = path.join(gitDir, `open-swe-index-${randomUUID()}`)
  const env = { ...process.env, GIT_INDEX_FILE: indexFile }
  try {
    const hasHead = await ok(git(repo, ["rev-parse", "--verify", "-q", "HEAD"], env))
    await git(repo, ["read-tree", ...(hasHead ? ["HEAD"] : ["--empty"])], env)
    await git(repo, ["add", "-A", "--", "."], env)
    return text(await git(repo, ["write-tree"], env))
  } finally {
    fs.rmSync(indexFile, { force: true })
  }
}

async function captureCheckpoint(repo, ref) {
  const tree = await writeWorktreeTree(repo)
  const hasHead = await ok(git(repo, ["rev-parse", "--verify", "-q", "HEAD"]))
  const commit = text(
    await git(
      repo,
      ["commit-tree", tree, ...(hasHead ? ["-p", "HEAD"] : []), "-m", "open-swe-local-turn"],
      {
        ...process.env,
        GIT_AUTHOR_NAME: "Open SWE",
        GIT_AUTHOR_EMAIL: "open-swe@users.noreply.github.com",
        GIT_COMMITTER_NAME: "Open SWE",
        GIT_COMMITTER_EMAIL: "open-swe@users.noreply.github.com",
      }
    )
  )
  await git(repo, ["update-ref", ref, commit])
  return ref
}

/** Synchronous so it can also run from Electron's `before-quit`. */
function deleteRefs(repo, refs) {
  for (const ref of refs) {
    try {
      execFileSync("git", ["update-ref", "-d", ref], { cwd: repo, stdio: "ignore" })
    } catch {}
  }
}

/** Checkpoint refs in `repo` that no live session owns any more. */
async function staleRefs(repo, liveRefs) {
  const listed = await git(repo, [
    "for-each-ref",
    "--format=%(refname)",
    CHECKPOINT_NAMESPACE,
  ]).catch(() => Buffer.alloc(0))
  return text(listed)
    .split("\n")
    .filter((ref) => ref && !liveRefs.includes(ref))
}

function parseNumstat(raw) {
  const stats = []
  for (const record of raw.split("\0")) {
    const parts = record.split("\t")
    if (parts.length !== 3 || !parts[2]) continue
    stats.push({
      path: parts[2],
      additions: parts[0] === "-" ? null : Number(parts[0]),
      deletions: parts[1] === "-" ? null : Number(parts[1]),
    })
  }
  return stats
}

function parseNameStatus(raw) {
  const fields = raw.split("\0").filter(Boolean)
  const statuses = new Map()
  for (let i = 0; i + 1 < fields.length; i += 2) {
    const kind = fields[i][0]
    statuses.set(fields[i + 1], kind === "A" ? "added" : kind === "D" ? "removed" : "modified")
  }
  return statuses
}

/** Both sides of every changed path in one `cat-file --batch`; `false` means too large. */
async function readBlobs(repo, base, head, paths) {
  const specs = paths.flatMap((file) => [`${base}:${file}`, `${head}:${file}`])
  const output = await gitStdin(repo, ["cat-file", "--batch"], `${specs.join("\n")}\n`)
  const blobs = []
  let at = 0
  for (let i = 0; i < specs.length; i++) {
    const end = output.indexOf("\n", at)
    if (end < 0) {
      blobs.push(null)
      continue
    }
    const header = output.subarray(at, end).toString("utf8").split(" ")
    at = end + 1
    if (header.length < 3) {
      blobs.push(null)
      continue
    }
    const size = Number(header[2])
    blobs.push(size <= MAX_FILE_BYTES ? output.subarray(at, at + size) : false)
    at += size + 1
  }
  return new Map(paths.map((file, i) => [file, { base: blobs[i * 2], head: blobs[i * 2 + 1] }]))
}

function decode(blob) {
  if (blob === false) return [null, true]
  if (!Buffer.isBuffer(blob)) return [null, false]
  const content = blob.toString("utf8")
  return content.includes("\u0000") ? [null, true] : [content, false]
}

/** Files changed between `base` and the live worktree, shaped like the cloud turn diff. */
async function readDiff(repo, base) {
  const head = await writeWorktreeTree(repo)
  const range = ["--no-renames", base, head]
  const numstat = (await git(repo, ["diff", "--numstat", "-z", ...range])).toString("utf8")
  const nameStatus = (await git(repo, ["diff", "--name-status", "-z", ...range])).toString("utf8")
  const stats = parseNumstat(numstat)
  const statuses = parseNameStatus(nameStatus)
  const shown = stats.slice(0, MAX_FILES)
  const blobs = shown.length
    ? await readBlobs(repo, base, head, shown.map((stat) => stat.path))
    : new Map()

  return {
    status: "ready",
    truncated: stats.length > MAX_FILES,
    files: shown.map((stat) => {
      const sides = blobs.get(stat.path) || {}
      const [originalContent, badBase] = decode(sides.base)
      const [modifiedContent, badHead] = decode(sides.head)
      return {
        path: stat.path,
        previousPath: null,
        status: statuses.get(stat.path) || "modified",
        additions: stat.additions ?? 0,
        deletions: stat.deletions ?? 0,
        originalContent,
        modifiedContent,
        unrenderable: stat.additions === null || badBase || badHead,
      }
    }),
  }
}

module.exports = {
  captureCheckpoint,
  checkpointRef,
  deleteRefs,
  readDiff,
  repoRoot,
  staleRefs,
}
