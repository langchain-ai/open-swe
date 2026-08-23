import { execFileSync } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"

import {
  captureCheckpoint,
  checkoutBranch,
  checkpointRef,
  currentBranch,
  localBranches,
  parsePullRequest,
  readBranchDiff,
  readDiff,
  repoRoot,
} from "./git-diff"

const temporary: Array<string> = []

afterEach(() => {
  for (const dir of temporary.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

function git(cwd: string, args: Array<string>) {
  execFileSync("git", args, { cwd, stdio: "ignore" })
}

/** A repository of its own, so a test never touches the developer's checkout. */
function repository() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-git-"))
  temporary.push(dir)
  git(dir, ["init", "-q", "-b", "main"])
  git(dir, ["config", "user.email", "test@example.com"])
  git(dir, ["config", "user.name", "Test"])
  return dir
}

describe("pull request metadata", () => {
  it("normalizes a validated payload", () => {
    expect(
      parsePullRequest(
        JSON.stringify({
          number: 12,
          title: "Draft change",
          state: "OPEN",
          isDraft: true,
          headRefName: "feature",
          baseRefName: "main",
          url: "https://github.com/example/repo/pull/12",
          author: { login: "octocat" },
          createdAt: "2026-08-20T00:00:00Z",
          changedFiles: 3,
          additions: 10,
          deletions: 2,
        })
      )
    ).toEqual({
      number: 12,
      title: "Draft change",
      state: "draft",
      headRef: "feature",
      baseRef: "main",
      url: "https://github.com/example/repo/pull/12",
      repoFullName: "example/repo",
      author: "octocat",
      authorAvatarUrl: null,
      createdAt: "2026-08-20T00:00:00Z",
      diffStats: { files: 3, additions: 10, deletions: 2 },
    })
  })

  it("only calls an open pull request a draft", () => {
    expect(
      parsePullRequest(
        JSON.stringify({
          number: 12,
          title: "Closed draft",
          state: "CLOSED",
          isDraft: true,
          headRefName: "feature",
          baseRefName: "main",
          url: "https://github.com/example/repo/pull/12",
        })
      )?.state
    ).toBe("closed")
  })

  it("rejects a non-http url", () => {
    expect(parsePullRequest('{"url":"javascript:alert(1)"}')).toBeNull()
  })
})

it("diffs the worktree against a session checkpoint", async () => {
  const dir = repository()
  fs.writeFileSync(path.join(dir, "kept.txt"), "one\ntwo\n")
  fs.writeFileSync(path.join(dir, "gone.txt"), "bye\n")
  git(dir, ["add", "-A"])
  git(dir, ["commit", "-qm", "init"])

  const repo = (await repoRoot(dir))!
  expect(await currentBranch(dir)).toBe("main")
  await checkoutBranch(dir, "feature", true)
  expect(await currentBranch(dir)).toBe("feature")
  expect(await localBranches(dir)).toEqual(["feature", "main"])
  await checkoutBranch(dir, "main")
  const ref = checkpointRef("session-id")
  await captureCheckpoint(repo, ref)

  fs.writeFileSync(path.join(dir, "kept.txt"), "one\ntwo\nthree\n")
  fs.writeFileSync(path.join(dir, "added.txt"), "fresh\n")
  fs.writeFileSync(path.join(dir, "binary.dat"), Buffer.from([0, 1, 2, 0]))
  fs.writeFileSync(path.join(dir, "huge.txt"), "x".repeat(500_000))
  fs.rmSync(path.join(dir, "gone.txt"))

  const diff = await readDiff(repo, ref)
  expect(diff.status).toBe("ready")
  expect(diff.truncated).toBe(false)
  expect(
    diff.files.map((file) => [
      file.path,
      file.status,
      file.additions,
      file.deletions,
    ])
  ).toEqual([
    ["added.txt", "added", 1, 0],
    ["binary.dat", "added", 0, 0],
    ["gone.txt", "removed", 0, 1],
    ["huge.txt", "added", 1, 0],
    ["kept.txt", "modified", 1, 0],
  ])

  const byPath = new Map(diff.files.map((file) => [file.path, file]))
  expect(byPath.get("kept.txt")).toMatchObject({
    originalContent: "one\ntwo\n",
    modifiedContent: "one\ntwo\nthree\n",
    unrenderable: false,
  })
  expect(byPath.get("binary.dat")?.unrenderable).toBe(true)
  expect(byPath.get("gone.txt")?.modifiedContent).toBeNull()

  // Oversized blobs are never read into memory, only reported.
  expect(byPath.get("huge.txt")).toMatchObject({
    unrenderable: true,
    modifiedContent: null,
  })
})

it("reports only what the branch committed", async () => {
  const dir = repository()
  fs.writeFileSync(path.join(dir, "models.ts"), "one\n")
  fs.writeFileSync(path.join(dir, "search.ts"), "search\n")
  git(dir, ["add", "-A"])
  git(dir, ["commit", "-qm", "init"])

  const repo = (await repoRoot(dir))!
  await checkoutBranch(dir, "feature", true)
  fs.writeFileSync(path.join(dir, "models.ts"), "one\ntwo\n")
  git(dir, ["add", "-A"])
  git(dir, ["commit", "-qm", "feature work"])

  // Another session dirties the shared worktree; none of it is this branch's.
  fs.writeFileSync(path.join(dir, "search.ts"), "search\nelsewhere\n")
  fs.writeFileSync(path.join(dir, "stray.txt"), "stray\n")

  const diff = await readBranchDiff(repo, "main")
  expect(diff.status).toBe("ready")
  expect(
    diff.files.map((file) => [file.path, file.status, file.additions])
  ).toEqual([["models.ts", "modified", 1]])

  expect((await readBranchDiff(repo, "no-such-branch")).status).toBe("missing")
  expect((await readBranchDiff(repo, "--upload-pack=touch")).status).toBe(
    "missing"
  )

  // The thread's branch is reported even while another one holds the checkout.
  await checkoutBranch(dir, "main")
  fs.writeFileSync(path.join(dir, "search.ts"), "search\nmain work\n")
  git(dir, ["add", "-A"])
  git(dir, ["commit", "-qm", "main work"])

  expect(
    (await readBranchDiff(repo, "main", "feature")).files.map((f) => f.path)
  ).toEqual(["models.ts"])
  expect((await readBranchDiff(repo, "main", "no-such-branch")).status).toBe(
    "missing"
  )
  expect(
    (await readBranchDiff(repo, "main", "--upload-pack=touch")).status
  ).toBe("missing")
})
