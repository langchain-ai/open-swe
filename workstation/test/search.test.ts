import assert from "node:assert/strict"
import {
  chmod,
  mkdir,
  mkdtemp,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import type { TestContext } from "node:test"
import { test } from "node:test"

import { glob, grep } from "../src/backend/search.ts"
import type { GlobResult, GrepResult } from "../src/backend/types.ts"

async function makeTree(
  t: TestContext,
  files: Readonly<Record<string, string | Uint8Array>>
): Promise<string> {
  const created = await mkdtemp(path.join(tmpdir(), "workstation-search-"))
  const root = await realpath(created)
  for (const [relativePath, content] of Object.entries(files)) {
    const target = path.join(root, relativePath)
    await mkdir(path.dirname(target), { recursive: true })
    await writeFile(target, content)
  }
  t.after(async () => {
    await rm(created, { recursive: true, force: true })
  })
  return root
}

function relativeMatches(root: string, result: GlobResult): string[] {
  return (result.matches ?? []).map((match) => {
    assert.ok(path.isAbsolute(match.path), `${match.path} is not absolute`)
    return path.relative(root, match.path)
  })
}

function matchedLines(result: GrepResult): Array<[string, number]> {
  return (result.matches ?? []).map((match) => [
    path.basename(match.path),
    match.line,
  ])
}

const TREE: Readonly<Record<string, string>> = {
  "top.py": "print(1)\n",
  "top.yml": "a: 1\n",
  "src/main.py": "print(2)\n",
  "src/app/main.py": "print(3)\n",
  "docs/readme.md": "# docs\n",
  ".github/workflows/ci.yml": "on: push\n",
}

test("a pattern without a slash matches the basename at any depth", async (t) => {
  const root = await makeTree(t, TREE)
  const result = await glob(root, "*.py")
  assert.deepEqual(relativeMatches(root, result), [
    "src/app/main.py",
    "src/main.py",
    "top.py",
  ])
  assert.equal(result.truncated, false)
})

test("a pattern with a slash matches the relative path, with globstar", async (t) => {
  const root = await makeTree(t, TREE)
  assert.deepEqual(relativeMatches(root, await glob(root, "src/**/*.py")), [
    "src/app/main.py",
    "src/main.py",
  ])
  assert.deepEqual(relativeMatches(root, await glob(root, "src/*.py")), [
    "src/main.py",
  ])
})

test("a leading slash anchors to the search root and narrows", async (t) => {
  const root = await makeTree(t, TREE)
  assert.deepEqual(relativeMatches(root, await glob(root, "/*.py")), ["top.py"])
})

test("dot names need an explicit dot and globstar does not descend into them", async (t) => {
  const root = await makeTree(t, TREE)
  assert.deepEqual(relativeMatches(root, await glob(root, "*.yml")), [
    ".github/workflows/ci.yml",
    "top.yml",
  ])
  assert.deepEqual(relativeMatches(root, await glob(root, "**/*.yml")), [
    "top.yml",
  ])
  assert.deepEqual(
    relativeMatches(root, await glob(root, ".github/**/*.yml")),
    [".github/workflows/ci.yml"]
  )
})

test("only regular files are returned, never directories", async (t) => {
  const root = await makeTree(t, TREE)
  const result = await glob(root, "*")
  assert.deepEqual(relativeMatches(root, result), [
    ".github/workflows/ci.yml",
    "docs/readme.md",
    "src/app/main.py",
    "src/main.py",
    "top.py",
    "top.yml",
  ])
  for (const match of result.matches ?? []) {
    assert.equal(match.isDir, false)
    assert.equal(typeof match.size, "number")
  }
})

test("a pattern with a .. segment is reported as an error, not thrown", async (t) => {
  const root = await makeTree(t, TREE)
  const result = await glob(root, "../*.py")
  assert.match(result.error ?? "", /Path traversal not allowed/)
  assert.equal(result.matches, undefined)
  assert.equal(result.truncated, false)

  const grepped = await grep(root, "print", { glob: "a/../b.py" })
  assert.match(grepped.error ?? "", /Path traversal not allowed/)
  assert.deepEqual(grepped.matches, [])
})

test("brace expansion past the limit is reported as an error", async (t) => {
  const root = await makeTree(t, TREE)
  const pattern = "{a,b,c,d}{a,b,c,d}{a,b,c,d}{a,b,c,d}{a,b,c,d}.txt"
  const result = await glob(root, pattern)
  assert.match(result.error ?? "", /Pattern limit exceeded the limit of 1000/)
  assert.equal(result.matches, undefined)
  assert.deepEqual(relativeMatches(root, await glob(root, "*.{py,yml}")), [
    ".github/workflows/ci.yml",
    "src/app/main.py",
    "src/main.py",
    "top.py",
    "top.yml",
  ])
})

test("glob defaults to the root and accepts a subdirectory search path", async (t) => {
  const root = await makeTree(t, TREE)
  assert.deepEqual(relativeMatches(root, await glob(root, "/*.py", "/")), [
    "top.py",
  ])
  assert.deepEqual(relativeMatches(root, await glob(root, "*.py", "src")), [
    "src/app/main.py",
    "src/main.py",
  ])
  assert.deepEqual(await glob(root, "*.py", "missing"), {
    matches: [],
    truncated: false,
  })
})

test("grep matches a literal substring, never a regex", async (t) => {
  const root = await makeTree(t, {
    "literal.txt": "a.b*c here\nazbyyc there\naXbc elsewhere\n",
  })
  const result = await grep(root, "a.b*c")
  assert.deepEqual(matchedLines(result), [["literal.txt", 1]])
  assert.equal(result.matches?.[0]?.text, "a.b*c here")
  assert.equal(result.truncated, false)
})

test("maxCount caps matches across files and flags only a real overflow", async (t) => {
  const root = await makeTree(t, {
    "a.txt": "needle 1\nfiller\nneedle 2\n",
    "b.txt": "needle 3\n",
  })
  const all = await grep(root, "needle")
  assert.equal(all.matches?.length, 3)
  assert.equal(all.truncated, false)

  const exact = await grep(root, "needle", { maxCount: 3 })
  assert.equal(exact.matches?.length, 3)
  assert.equal(exact.truncated, false)

  const capped = await grep(root, "needle", { maxCount: 2 })
  assert.equal(capped.matches?.length, 2)
  assert.equal(capped.truncated, true)
})

test("contextLines is bounded by the file edges and never repeats a match line", async (t) => {
  const root = await makeTree(t, {
    "ctx.txt": "hit one\nfiller a\nhit two\nhit three\nfiller b\n",
  })
  const result = await grep(root, "hit", { contextLines: 1 })
  assert.equal(result.matches?.length, 3)
  const [first, second, third] = result.matches ?? []
  assert.deepEqual(first?.contextBefore, [])
  assert.deepEqual(first?.contextAfter, [{ line: 2, text: "filler a" }])
  assert.deepEqual(second?.contextBefore, [{ line: 2, text: "filler a" }])
  assert.deepEqual(second?.contextAfter, [])
  assert.deepEqual(third?.contextBefore, [])
  assert.deepEqual(third?.contextAfter, [{ line: 5, text: "filler b" }])
})

test("context fields are absent when no context was requested", async (t) => {
  const root = await makeTree(t, { "ctx.txt": "one\nhit\nthree\n" })
  const result = await grep(root, "hit")
  assert.equal(result.matches?.[0]?.contextBefore, undefined)
  assert.equal(result.matches?.[0]?.contextAfter, undefined)
})

test("a binary file is skipped rather than yielding garbage matches", async (t) => {
  const root = await makeTree(t, {
    "text.txt": "needle here\n",
    "blob.bin": new Uint8Array([
      0xff, 0xfe, 0x00, 0x6e, 0x65, 0x65, 0x64, 0x6c, 0x65, 0x00, 0xc3, 0x28,
    ]),
  })
  const result = await grep(root, "needle")
  assert.deepEqual(matchedLines(result), [["text.txt", 1]])
  assert.equal(result.error, undefined)
})

test("an include glob filters which files are searched", async (t) => {
  const root = await makeTree(t, {
    "keep.py": "needle\n",
    "skip.txt": "needle\n",
    "src/deep.py": "needle\n",
  })
  assert.deepEqual(matchedLines(await grep(root, "needle", { glob: "*.py" })), [
    ["keep.py", 1],
    ["deep.py", 1],
  ])
  assert.deepEqual(
    matchedLines(await grep(root, "needle", { glob: "/*.py" })),
    [["keep.py", 1]]
  )
})

test("grep searches a single file path directly", async (t) => {
  const root = await makeTree(t, {
    "a.txt": "needle\n",
    "b.txt": "needle\n",
  })
  assert.deepEqual(
    matchedLines(await grep(root, "needle", { path: "a.txt" })),
    [["a.txt", 1]]
  )
  assert.deepEqual(await grep(root, "needle", { path: "missing.txt" }), {
    matches: [],
    truncated: false,
  })
})

test("both functions refuse a path outside the root", async (t) => {
  const root = await makeTree(t, TREE)
  const globbed = await glob(root, "*.py", "..")
  assert.match(globbed.error ?? "", /outside the workstation root/)
  assert.deepEqual(globbed.matches, [])

  const grepped = await grep(root, "print", { path: "/etc" })
  assert.match(grepped.error ?? "", /outside the workstation root/)
  assert.deepEqual(grepped.matches, [])
  assert.equal(grepped.truncated, false)
})

test("a symlink pointing out of the root is not followed", async (t) => {
  const outside = await makeTree(t, { "secret.py": "needle\n" })
  const root = await makeTree(t, { "inside.py": "needle\n" })
  await symlink(path.join(outside, "secret.py"), path.join(root, "link.py"))
  await symlink(outside, path.join(root, "linked-dir"))

  assert.deepEqual(relativeMatches(root, await glob(root, "*.py")), [
    "inside.py",
  ])
  assert.deepEqual(matchedLines(await grep(root, "needle")), [["inside.py", 1]])
})

test("an unreadable subtree truncates glob and is reported by grep", async (t) => {
  const root = await makeTree(t, {
    "open.py": "needle\n",
    "closed/hidden.py": "needle\n",
  })
  const closed = path.join(root, "closed")
  await chmod(closed, 0o000)
  let globbed: GlobResult
  let grepped: GrepResult
  try {
    globbed = await glob(root, "*.py")
    grepped = await grep(root, "needle")
  } finally {
    // Restored before the fixture teardown, which cannot remove a directory it
    // is not allowed to read.
    await chmod(closed, 0o755)
  }

  assert.deepEqual(relativeMatches(root, globbed), ["open.py"])
  assert.equal(globbed.truncated, true)
  assert.equal(globbed.truncationReason, "unreadable")
  assert.deepEqual(matchedLines(grepped), [["open.py", 1]])
  assert.match(grepped.error ?? "", /unreadable/)
})

test("reports a negative context width instead of throwing", async (t) => {
  const root = await makeTree(t, { "a.txt": "needle\n" })
  const result = await grep(root, "needle", { contextLines: -1 })
  assert.match(
    result.error ?? "",
    /contextLines must be a non-negative integer/
  )
  assert.deepEqual(result.matches, [])
  assert.equal(result.truncated, false)
})
